from django.urls import path

from forensics.views import (
    ArtifactDownloadView,
    CaseAnalysisPartialView,
    CancelCaseView,
    CaseDetailView,
    CaseStatusPartialView,
    CaseTimelinePartialView,
    DashboardIntegrityPartialView,
    DashboardOperationalPartialView,
    LinguisticAnalysisView,
    PhoneticAnalysisView,
    RegenerateReportView,
    ReportDetailView,
    ReportDownloadView,
    ReportVersionsPartialView,
    RetryCaseView,
    ReportsView,
    ReviewDecisionView,
)


urlpatterns = [
    path("phonetic/", PhoneticAnalysisView.as_view(), name="phonetic_analysis"),
    path("linguistic/", LinguisticAnalysisView.as_view(), name="linguistic_analysis"),
    path("cases/<uuid:case_id>/", CaseDetailView.as_view(), name="case_detail"),
    path("cases/<uuid:case_id>/status/", CaseStatusPartialView.as_view(), name="case_status_partial"),
    path("cases/<uuid:case_id>/timeline/", CaseTimelinePartialView.as_view(), name="case_timeline_partial"),
    path("cases/<uuid:case_id>/analysis/", CaseAnalysisPartialView.as_view(), name="case_analysis_partial"),
    path("cases/<uuid:case_id>/reports/", ReportVersionsPartialView.as_view(), name="report_versions_partial"),
    path("cases/<uuid:case_id>/review/", ReviewDecisionView.as_view(), name="case_review"),
    path("cases/<uuid:case_id>/cancel/", CancelCaseView.as_view(), name="case_cancel"),
    path("cases/<uuid:case_id>/retry/", RetryCaseView.as_view(), name="case_retry"),
    path("cases/<uuid:case_id>/reports/regenerate/", RegenerateReportView.as_view(), name="report_regenerate"),
    path("reports/", ReportsView.as_view(), name="reports"),
    path("reports/<uuid:report_id>/", ReportDetailView.as_view(), name="report_detail"),
    path("reports/version/<uuid:version_id>/download/", ReportDownloadView.as_view(), name="report_download"),
    path("artifacts/<uuid:artifact_id>/download/", ArtifactDownloadView.as_view(), name="artifact_download"),
    path("partials/dashboard/operational/", DashboardOperationalPartialView.as_view(), name="dashboard_operational_partial"),
    path("partials/dashboard/integrity/", DashboardIntegrityPartialView.as_view(), name="dashboard_integrity_partial"),
]
