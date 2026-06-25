from __future__ import annotations

from celery import current_app
from django.db.models import Avg, Count, Q

from forensics.models import AnalysisJob, Case, EvidenceArtifact, EventLog, ReportVersion


def dashboard_metrics() -> dict:
    cases = Case.objects.all()
    return {
        "total_cases": cases.count(),
        "total_completed": cases.filter(status=Case.Status.COMPLETED).count(),
        "total_pending": cases.filter(status__in=[Case.Status.QUEUED, Case.Status.RUNNING, Case.Status.AWAITING_REVIEW]).count(),
        "total_phonetic": cases.filter(case_type=Case.CaseType.PHONETIC).count(),
        "total_linguistic": cases.filter(case_type=Case.CaseType.LINGUISTIC).count(),
        "total_reports": ReportVersion.objects.filter(status=ReportVersion.Status.READY).count(),
        "total_failed_jobs": AnalysisJob.objects.filter(status=AnalysisJob.Status.FAILED).count(),
        "total_adverse_condition_cases": cases.filter(adverse_condition_flag=True).count(),
    }


def recent_cases(limit: int = 12):
    return (
        Case.objects.select_related("created_by", "reviewer")
        .prefetch_related("jobs", "report__versions")
        .order_by("-updated_at")[:limit]
    )


def operational_summary() -> dict:
    job_counts = AnalysisJob.objects.aggregate(
        running=Count("id", filter=Q(status=AnalysisJob.Status.RUNNING)),
        failed=Count("id", filter=Q(status=AnalysisJob.Status.FAILED)),
        retrying=Count("id", filter=Q(status=AnalysisJob.Status.RETRYING)),
        pending=Count("id", filter=Q(status=AnalysisJob.Status.PENDING)),
        avg_duration=Avg("duration_seconds"),
    )
    recent_failures = AnalysisJob.objects.filter(status=AnalysisJob.Status.FAILED).select_related("case")[:5]

    worker_status = "unknown"
    active_tasks = {}
    try:
        inspect = current_app.control.inspect(timeout=1)
        stats = inspect.stats() or {}
        active_tasks = inspect.active() or {}
        worker_status = "online" if stats else "offline"
    except Exception:
        worker_status = "offline"

    return {
        "worker_status": worker_status,
        "queue_summary": job_counts,
        "active_tasks": active_tasks,
        "recent_failures": recent_failures,
    }


def integrity_summary() -> dict:
    return {
        "recent_hashes": EvidenceArtifact.objects.filter(is_original=True).order_by("-created_at")[:6],
        "recent_reports": ReportVersion.objects.order_by("-generated_at")[:6],
        "recent_audit_events": EventLog.objects.order_by("-created_at")[:8],
    }


def adverse_condition_summary():
    return Case.objects.filter(adverse_condition_flag=True).order_by("-updated_at")[:8]
