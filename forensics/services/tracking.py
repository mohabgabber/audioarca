from __future__ import annotations

from django.utils import timezone

from forensics.models import AnalysisJob, Case
from forensics.services.helpers import log_event


def set_current_job(job: AnalysisJob) -> None:
    AnalysisJob.objects.filter(case=job.case, is_current=True).exclude(pk=job.pk).update(is_current=False)
    if not job.is_current:
        job.is_current = True
        job.save(update_fields=["is_current", "updated_at"])


class JobTracker:
    """Centralized progress and state updates for analysis jobs."""

    def __init__(self, job: AnalysisJob):
        self.job = job
        self.case = job.case

    def _preserve_case_status(self) -> bool:
        return self.job.job_type == AnalysisJob.JobType.REPORT and self.case.is_analyzed

    def start(self, stage: str, message: str) -> None:
        now = timezone.now()
        set_current_job(self.job)
        self.job.status = AnalysisJob.Status.RUNNING
        self.job.stage = stage
        self.job.started_at = self.job.started_at or now
        self.job.task_state = "STARTED"
        self.job.save(update_fields=["status", "stage", "started_at", "task_state", "updated_at"])
        if not self._preserve_case_status():
            self.case.status = Case.Status.RUNNING
        self.case.current_stage = stage
        self.case.analysis_started_at = self.case.analysis_started_at or now
        self.case.task_state = "STARTED"
        self.case.save(update_fields=["status", "current_stage", "analysis_started_at", "task_state", "updated_at"])
        log_event(
            event_type="task",
            title=f"{self.job.get_job_type_display()} started",
            message=message,
            case=self.case,
            details={"stage": stage, "job_id": str(self.job.id)},
        )

    def update(self, stage: str, progress: int, message: str, metadata: dict | None = None) -> None:
        progress = max(0, min(progress, 100))
        set_current_job(self.job)
        self.job.status = AnalysisJob.Status.RUNNING
        self.job.stage = stage
        self.job.progress_percentage = progress
        self.job.log_excerpt = message
        self.job.task_state = self.job.task_state or "STARTED"
        if metadata:
            updated = self.job.metadata or {}
            updated.update(metadata)
            self.job.metadata = updated
        self.job.save(
            update_fields=[
                "status",
                "stage",
                "progress_percentage",
                "log_excerpt",
                "task_state",
                "metadata",
                "updated_at",
            ]
        )
        self.case.current_stage = stage
        self.case.progress_percentage = progress
        self.case.task_state = self.job.task_state or "STARTED"
        self.case.save(update_fields=["status", "current_stage", "progress_percentage", "task_state", "updated_at"])
        log_event(
            event_type="task",
            title=f"{self.job.get_job_type_display()} progress",
            message=message,
            case=self.case,
            details={"stage": stage, "progress": progress, "job_id": str(self.job.id)},
        )

    def retry(self, *, stage: str, message: str) -> None:
        set_current_job(self.job)
        self.job.status = AnalysisJob.Status.RETRYING
        self.job.stage = stage
        self.job.task_state = "RETRY"
        self.job.retry_count += 1
        self.job.log_excerpt = message
        self.job.save(
            update_fields=[
                "status",
                "stage",
                "task_state",
                "retry_count",
                "log_excerpt",
                "updated_at",
            ]
        )
        if not self._preserve_case_status():
            self.case.status = Case.Status.RUNNING
        self.case.current_stage = stage
        self.case.task_state = "RETRY"
        self.case.failure_reason = ""
        self.case.save(update_fields=["status", "current_stage", "task_state", "failure_reason", "updated_at"])
        log_event(
            event_type="task",
            title=f"{self.job.get_job_type_display()} retry scheduled",
            message=message,
            case=self.case,
            details={"stage": stage, "job_id": str(self.job.id), "retry_count": self.job.retry_count},
        )

    def succeed(self, *, stage: str, message: str) -> None:
        now = timezone.now()
        duration = None
        if self.job.started_at:
            duration = (now - self.job.started_at).total_seconds()
        set_current_job(self.job)
        self.job.status = AnalysisJob.Status.SUCCEEDED
        self.job.stage = stage
        self.job.progress_percentage = 100
        self.job.task_state = "SUCCESS"
        self.job.finished_at = now
        self.job.duration_seconds = duration
        self.job.log_excerpt = message
        self.job.save(
            update_fields=[
                "status",
                "stage",
                "progress_percentage",
                "task_state",
                "finished_at",
                "duration_seconds",
                "log_excerpt",
                "updated_at",
            ]
        )
        self.case.progress_percentage = 100
        self.case.current_stage = stage
        self.case.task_state = "SUCCESS"
        self.case.analysis_completed_at = now
        self.case.save(
            update_fields=[
                "progress_percentage",
                "current_stage",
                "task_state",
                "analysis_completed_at",
                "updated_at",
            ]
        )
        log_event(
            event_type="task",
            title=f"{self.job.get_job_type_display()} completed",
            message=message,
            case=self.case,
            details={"stage": stage, "job_id": str(self.job.id)},
        )

    def fail(self, *, stage: str, message: str) -> None:
        now = timezone.now()
        duration = None
        if self.job.started_at:
            duration = (now - self.job.started_at).total_seconds()
        set_current_job(self.job)
        self.job.status = AnalysisJob.Status.FAILED
        self.job.stage = stage
        self.job.task_state = "FAILURE"
        self.job.error_message = message
        self.job.finished_at = now
        self.job.duration_seconds = duration
        self.job.log_excerpt = message
        self.job.save(
            update_fields=[
                "status",
                "stage",
                "task_state",
                "error_message",
                "finished_at",
                "duration_seconds",
                "log_excerpt",
                "updated_at",
            ]
        )
        if not self._preserve_case_status():
            self.case.status = Case.Status.FAILED
        self.case.failure_reason = message
        self.case.task_state = "FAILURE"
        self.case.current_stage = stage
        self.case.save(update_fields=["status", "failure_reason", "task_state", "current_stage", "updated_at"])
        log_event(
            event_type="task",
            title=f"{self.job.get_job_type_display()} failed",
            message=message,
            case=self.case,
            details={"stage": stage, "job_id": str(self.job.id)},
        )

    def cancel(self, *, message: str) -> None:
        set_current_job(self.job)
        self.job.status = AnalysisJob.Status.CANCELLED
        self.job.task_state = "REVOKED"
        self.job.error_message = message
        self.job.finished_at = timezone.now()
        self.job.save(update_fields=["status", "task_state", "error_message", "finished_at", "updated_at"])
        if not self._preserve_case_status():
            self.case.status = Case.Status.CANCELLED
        self.case.failure_reason = message
        self.case.task_state = "REVOKED"
        self.case.save(update_fields=["status", "failure_reason", "task_state", "updated_at"])
        log_event(
            event_type="task",
            title=f"{self.job.get_job_type_display()} cancelled",
            message=message,
            case=self.case,
            details={"job_id": str(self.job.id)},
        )
