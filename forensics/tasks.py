from __future__ import annotations

from datetime import timedelta

from celery import shared_task
from django.db import DataError
from django.utils import timezone

from dash.models import Invitation
from forensics.models import AnalysisJob, Case
from forensics.services.analysis import (
    AnalysisCancelled,
    ModelAssetPermissionError,
    run_linguistic_pipeline,
    run_phonetic_pipeline,
)
from forensics.services.reporting import ensure_report_version
from forensics.services.tracking import JobTracker


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 2})
def run_phonetic_analysis(self, case_id: str, job_id: str):
    job = AnalysisJob.objects.select_related("case").get(pk=job_id)
    case = Case.objects.get(pk=case_id)
    tracker = JobTracker(job)
    job.celery_task_id = self.request.id
    job.task_state = "STARTED"
    job.save(update_fields=["celery_task_id", "task_state", "updated_at"])
    case.celery_task_id = self.request.id
    case.task_state = "STARTED"
    case.save(update_fields=["celery_task_id", "task_state", "updated_at"])
    try:
        return str(run_phonetic_pipeline(case, job).pk)
    except AnalysisCancelled as exc:
        tracker.cancel(message=str(exc))
        return None
    except (ModelAssetPermissionError, PermissionError) as exc:
        tracker.fail(stage=job.stage or AnalysisJob.Stage.FAILED, message=str(exc))
        return None
    except DataError as exc:
        tracker.fail(stage=job.stage or AnalysisJob.Stage.FAILED, message=str(exc))
        return None
    except Exception as exc:
        if self.request.retries < self.max_retries:
            tracker.retry(stage=job.stage or AnalysisJob.Stage.FAILED, message=str(exc))
        else:
            tracker.fail(stage=job.stage or AnalysisJob.Stage.FAILED, message=str(exc))
        raise


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 2})
def run_linguistic_analysis(self, case_id: str, job_id: str):
    job = AnalysisJob.objects.select_related("case").get(pk=job_id)
    case = Case.objects.get(pk=case_id)
    tracker = JobTracker(job)
    job.celery_task_id = self.request.id
    job.task_state = "STARTED"
    job.save(update_fields=["celery_task_id", "task_state", "updated_at"])
    case.celery_task_id = self.request.id
    case.task_state = "STARTED"
    case.save(update_fields=["celery_task_id", "task_state", "updated_at"])
    try:
        return str(run_linguistic_pipeline(case, job).pk)
    except AnalysisCancelled as exc:
        tracker.cancel(message=str(exc))
        return None
    except (ModelAssetPermissionError, PermissionError) as exc:
        tracker.fail(stage=job.stage or AnalysisJob.Stage.FAILED, message=str(exc))
        return None
    except DataError as exc:
        tracker.fail(stage=job.stage or AnalysisJob.Stage.FAILED, message=str(exc))
        return None
    except Exception as exc:
        if self.request.retries < self.max_retries:
            tracker.retry(stage=job.stage or AnalysisJob.Stage.FAILED, message=str(exc))
        else:
            tracker.fail(stage=job.stage or AnalysisJob.Stage.FAILED, message=str(exc))
        raise


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 2})
def generate_report_version(self, case_id: str, job_id: str):
    job = AnalysisJob.objects.select_related("case").get(pk=job_id)
    case = Case.objects.get(pk=case_id)
    tracker = JobTracker(job)
    job.celery_task_id = self.request.id
    job.task_state = "STARTED"
    job.save(update_fields=["celery_task_id", "task_state", "updated_at"])
    try:
        tracker.start(AnalysisJob.Stage.REPORT_DRAFTING, "Generating a new mandatory report version from structured evidence.")
        ensure_report_version(case=case, job=job)
        tracker.update(AnalysisJob.Stage.PDF_GENERATION, 95, "Report PDF rendered and stored.")
        tracker.succeed(stage=AnalysisJob.Stage.COMPLETED, message="Report regeneration completed.")
        if case.status != Case.Status.COMPLETED:
            case.status = Case.Status.AWAITING_REVIEW
            case.save(update_fields=["status", "updated_at"])
        return True
    except AnalysisCancelled as exc:
        tracker.cancel(message=str(exc))
        return None
    except Exception as exc:
        if self.request.retries < self.max_retries:
            tracker.retry(stage=job.stage or AnalysisJob.Stage.FAILED, message=str(exc))
        else:
            tracker.fail(stage=job.stage or AnalysisJob.Stage.FAILED, message=str(exc))
        raise


@shared_task
def cleanup_expired_invitations():
    expired = Invitation.objects.filter(status=Invitation.Status.PENDING, expires_at__lte=timezone.now())
    count = 0
    for invitation in expired:
        invitation.status = Invitation.Status.EXPIRED
        invitation.save(update_fields=["status", "updated_at"])
        count += 1
    return count


@shared_task
def mark_stuck_jobs():
    cutoff = timezone.now() - timedelta(hours=3)
    stuck_jobs = AnalysisJob.objects.filter(status=AnalysisJob.Status.RUNNING, started_at__lt=cutoff)
    count = 0
    for job in stuck_jobs:
        tracker = JobTracker(job)
        tracker.fail(stage=job.stage or AnalysisJob.Stage.FAILED, message="Job marked as stuck by maintenance task.")
        count += 1
    return count
