from __future__ import annotations

from dataclasses import dataclass

from celery import current_app
from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from forensics.models import (
    AnalysisJob,
    Case,
    LinguisticCase,
    PhoneticCase,
    TextSample,
    UploadedAudioSample,
)
from forensics.services.helpers import (
    generate_case_number,
    hash_text_payload,
    log_event,
    persist_uploaded_artifact,
)
from forensics.services.tracking import set_current_job


ACTIVE_JOB_STATUSES = {
    AnalysisJob.Status.PENDING,
    AnalysisJob.Status.RUNNING,
    AnalysisJob.Status.RETRYING,
}


@dataclass
class CaseCreationResult:
    case: Case
    job: AnalysisJob


def queue_analysis_job(*, case: Case, job: AnalysisJob, task, queue_case_status: str | None = Case.Status.QUEUED) -> None:
    set_current_job(job)

    def _enqueue() -> None:
        async_result = task.delay(str(case.id), str(job.id))
        job.celery_task_id = async_result.id
        job.task_state = "PENDING"
        job.status = AnalysisJob.Status.PENDING
        job.save(update_fields=["celery_task_id", "task_state", "status", "updated_at"])
        case.celery_task_id = async_result.id
        case.task_state = "PENDING"
        if queue_case_status is not None:
            case.status = queue_case_status
        case.save(update_fields=["celery_task_id", "task_state", "status", "updated_at"])

    transaction.on_commit(_enqueue)


@transaction.atomic
def create_phonetic_case(*, user, cleaned_data) -> CaseCreationResult:
    case = Case.objects.create(
        case_number=cleaned_data["case_number"] or generate_case_number(Case.CaseType.PHONETIC),
        name=cleaned_data["case_name"],
        description=cleaned_data["description"],
        case_type=Case.CaseType.PHONETIC,
        status=Case.Status.QUEUED,
        current_stage=AnalysisJob.Stage.UPLOAD,
        progress_percentage=3,
        created_by=user,
    )
    phonetic_case = PhoneticCase.objects.create(case=case)
    original_a = persist_uploaded_artifact(
        case=case,
        uploaded_file=cleaned_data["sample_a"],
        artifact_type="original_audio",
        role="sample_a",
        created_by=user,
        is_original=True,
        processing_steps=["secure-ingest", "sha256"],
    )
    original_b = persist_uploaded_artifact(
        case=case,
        uploaded_file=cleaned_data["sample_b"],
        artifact_type="original_audio",
        role="sample_b",
        created_by=user,
        is_original=True,
        processing_steps=["secure-ingest", "sha256"],
    )
    UploadedAudioSample.objects.create(
        case=case,
        role=UploadedAudioSample.SampleRole.SAMPLE_A,
        original_artifact=original_a,
        mime_type=original_a.mime_type,
        extension=cleaned_data["sample_a"].name.split(".")[-1].lower(),
        preprocessing_steps=["secure-ingest"],
    )
    UploadedAudioSample.objects.create(
        case=case,
        role=UploadedAudioSample.SampleRole.SAMPLE_B,
        original_artifact=original_b,
        mime_type=original_b.mime_type,
        extension=cleaned_data["sample_b"].name.split(".")[-1].lower(),
        preprocessing_steps=["secure-ingest"],
    )
    job = AnalysisJob.objects.create(
        case=case,
        job_type=AnalysisJob.JobType.PHONETIC,
        status=AnalysisJob.Status.PENDING,
        stage=AnalysisJob.Stage.QUEUED,
        progress_percentage=5,
        metadata={"case_type": "phonetic", "phonetic_case_id": str(phonetic_case.pk)},
    )
    case.current_stage = AnalysisJob.Stage.QUEUED
    case.save(update_fields=["current_stage", "updated_at"])
    log_event(
        event_type="case",
        title="Phonetic case created",
        message="Phonetic case queued for local analysis.",
        case=case,
        actor=user,
        details={"job_id": str(job.id)},
    )
    from forensics.tasks import run_phonetic_analysis

    queue_analysis_job(case=case, job=job, task=run_phonetic_analysis)
    return CaseCreationResult(case=case, job=job)


@transaction.atomic
def create_linguistic_case(*, user, cleaned_data) -> CaseCreationResult:
    case = Case.objects.create(
        case_number=cleaned_data["case_number"] or generate_case_number(Case.CaseType.LINGUISTIC),
        name=cleaned_data["case_name"],
        description=cleaned_data["description"],
        case_type=Case.CaseType.LINGUISTIC,
        status=Case.Status.QUEUED,
        current_stage=AnalysisJob.Stage.UPLOAD,
        progress_percentage=5,
        created_by=user,
    )
    linguistic_case = LinguisticCase.objects.create(case=case)
    suspected_text = cleaned_data["suspected_sample_text"]
    provided_text = cleaned_data["provided_sample_text"]
    TextSample.objects.create(
        case=case,
        role=TextSample.SampleRole.SUSPECTED,
        raw_text=suspected_text,
        normalized_text=suspected_text.strip(),
        sha256=hash_text_payload(suspected_text),
        text_length=len(suspected_text),
        token_count=len(suspected_text.split()),
        preprocessing_steps=["sha256", "text-normalization"],
    )
    TextSample.objects.create(
        case=case,
        role=TextSample.SampleRole.PROVIDED,
        raw_text=provided_text,
        normalized_text=provided_text.strip(),
        sha256=hash_text_payload(provided_text),
        text_length=len(provided_text),
        token_count=len(provided_text.split()),
        preprocessing_steps=["sha256", "text-normalization"],
    )
    job = AnalysisJob.objects.create(
        case=case,
        job_type=AnalysisJob.JobType.LINGUISTIC,
        status=AnalysisJob.Status.PENDING,
        stage=AnalysisJob.Stage.QUEUED,
        progress_percentage=5,
        metadata={"case_type": "linguistic", "linguistic_case_id": str(linguistic_case.pk)},
    )
    case.current_stage = AnalysisJob.Stage.QUEUED
    case.save(update_fields=["current_stage", "updated_at"])
    log_event(
        event_type="case",
        title="Linguistic case created",
        message="Linguistic case queued for local analysis.",
        case=case,
        actor=user,
        details={"job_id": str(job.id)},
    )
    from forensics.tasks import run_linguistic_analysis

    queue_analysis_job(case=case, job=job, task=run_linguistic_analysis)
    return CaseCreationResult(case=case, job=job)


def invite_url_for_token(request, token: str) -> str:
    if request is None:
        scheme = "https" if not settings.DEBUG else "http"
        return f"{scheme}://{settings.APP_DOMAIN}{reverse('invitation_accept', kwargs={'token': token})}"
    return request.build_absolute_uri(reverse("invitation_accept", kwargs={"token": token}))


@transaction.atomic
def retry_case_analysis(case: Case, actor) -> AnalysisJob:
    if case.status != Case.Status.FAILED:
        raise ValueError("Only failed cases can be retried.")
    if case.jobs.filter(is_current=True, status__in=ACTIVE_JOB_STATUSES).exists():
        raise ValueError("An analysis job is already queued or running for this case.")

    if case.case_type == Case.CaseType.PHONETIC:
        if case.audio_samples.count() != 2:
            raise ValueError("Phonetic analysis retry requires two uploaded audio samples.")
        job_type = AnalysisJob.JobType.PHONETIC
        metadata = {"case_type": "phonetic", "manual_retry": True}
        from forensics.tasks import run_phonetic_analysis as task
    elif case.case_type == Case.CaseType.LINGUISTIC:
        if case.text_samples.count() != 2:
            raise ValueError("Linguistic analysis retry requires two text samples.")
        job_type = AnalysisJob.JobType.LINGUISTIC
        metadata = {"case_type": "linguistic", "manual_retry": True}
        from forensics.tasks import run_linguistic_analysis as task
    else:
        raise ValueError("Unsupported case type for retry.")

    job = AnalysisJob.objects.create(
        case=case,
        job_type=job_type,
        status=AnalysisJob.Status.PENDING,
        stage=AnalysisJob.Stage.QUEUED,
        progress_percentage=5,
        metadata=metadata,
    )
    case.status = Case.Status.QUEUED
    case.current_stage = AnalysisJob.Stage.QUEUED
    case.progress_percentage = 5
    case.task_state = "PENDING"
    case.failure_reason = ""
    case.celery_task_id = ""
    case.save(
        update_fields=[
            "status",
            "current_stage",
            "progress_percentage",
            "task_state",
            "failure_reason",
            "celery_task_id",
            "updated_at",
        ]
    )
    log_event(
        event_type="task",
        title="Analysis retry queued",
        message="Failed case analysis was requeued using the existing evidence.",
        case=case,
        actor=actor,
        details={"job_id": str(job.id), "case_type": case.case_type},
    )
    queue_analysis_job(case=case, job=job, task=task)
    return job


@transaction.atomic
def cancel_case(case: Case, actor) -> None:
    current_job = case.jobs.filter(is_current=True).first()
    if case.status in {Case.Status.COMPLETED, Case.Status.CANCELLED, Case.Status.FAILED}:
        raise ValueError("Only queued, running, or review-pending cases can be cancelled.")
    case.status = Case.Status.CANCELLED
    case.failure_reason = "Cancelled by user."
    case.task_state = "REVOKED"
    case.save(update_fields=["status", "failure_reason", "task_state", "updated_at"])
    if current_job:
        current_job.cancel_requested = True
        current_job.status = AnalysisJob.Status.CANCELLED
        current_job.task_state = "REVOKED"
        current_job.finished_at = timezone.now()
        current_job.save(
            update_fields=[
                "cancel_requested",
                "status",
                "task_state",
                "finished_at",
                "updated_at",
            ]
        )
        if current_job.celery_task_id:
            try:
                current_app.control.revoke(current_job.celery_task_id, terminate=False)
            except Exception:
                pass
    log_event(
        event_type="case",
        title="Case cancelled",
        message="Case processing was cancelled by a user.",
        case=case,
        actor=actor,
    )
