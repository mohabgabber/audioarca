from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import DetailView, TemplateView, View

from forensics.forms import LinguisticCaseCreateForm, PhoneticCaseCreateForm, ReviewDecisionForm
from forensics.models import AnalysisJob, Case, EvidenceArtifact, Report, ReportVersion
from forensics.services.cases import (
    ACTIVE_JOB_STATUSES,
    cancel_case,
    create_linguistic_case,
    create_phonetic_case,
    queue_analysis_job,
    retry_case_analysis,
)
from forensics.services.helpers import log_event
from forensics.services.monitoring import integrity_summary, operational_summary


class CaseCreateRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.can_create_cases()

    def handle_no_permission(self):
        messages.error(self.request, "You do not have permission to create analysis cases.")
        return redirect("dashboard")


class ReviewerRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.can_review_cases()

    def handle_no_permission(self):
        messages.error(self.request, "Sign in to review or confirm an analysis case.")
        return redirect("dashboard")


class ForensicsTemplateView(LoginRequiredMixin, TemplateView):
    current_section = ""
    page_heading = ""
    page_subtitle = ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["current_section"] = self.current_section
        context["page_heading"] = self.page_heading
        context["page_subtitle"] = self.page_subtitle
        return context


class PhoneticAnalysisView(CaseCreateRequiredMixin, ForensicsTemplateView):
    template_name = "forensics/phonetic/index.html"
    current_section = "phonetic"
    page_heading = "Phonetic Analysis"
    page_subtitle = "Queue, review, and inspect speaker-comparison cases."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = kwargs.get("form") or PhoneticCaseCreateForm()
        context["cases"] = (
            Case.objects.filter(case_type=Case.CaseType.PHONETIC)
            .select_related("created_by", "reviewer", "report")
            .prefetch_related("report__versions")
            .order_by("-updated_at")
        )
        return context

    def post(self, request, *args, **kwargs):
        form = PhoneticCaseCreateForm(request.POST, request.FILES)
        if form.is_valid():
            result = create_phonetic_case(user=request.user, cleaned_data=form.cleaned_data)
            detail_url = reverse("case_detail", kwargs={"case_id": result.case.pk})
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return JsonResponse({"redirect_url": detail_url, "case_id": str(result.case.pk)})
            messages.success(request, "Phonetic case created and queued for analysis.")
            return redirect(detail_url)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"errors": form.errors}, status=400)
        return self.render_to_response(self.get_context_data(form=form))


class LinguisticAnalysisView(CaseCreateRequiredMixin, ForensicsTemplateView):
    template_name = "forensics/linguistic/index.html"
    current_section = "linguistic"
    page_heading = "Linguistic Analysis"
    page_subtitle = "Queue, review, and inspect stylometric comparison cases."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = kwargs.get("form") or LinguisticCaseCreateForm()
        context["cases"] = (
            Case.objects.filter(case_type=Case.CaseType.LINGUISTIC)
            .select_related("created_by", "reviewer", "report")
            .prefetch_related("report__versions")
            .order_by("-updated_at")
        )
        return context

    def post(self, request, *args, **kwargs):
        form = LinguisticCaseCreateForm(request.POST)
        if form.is_valid():
            result = create_linguistic_case(user=request.user, cleaned_data=form.cleaned_data)
            detail_url = reverse("case_detail", kwargs={"case_id": result.case.pk})
            messages.success(request, "Linguistic case created and queued for analysis.")
            return redirect(detail_url)
        return self.render_to_response(self.get_context_data(form=form))


class CaseDetailView(LoginRequiredMixin, DetailView):
    template_name = "forensics/cases/detail.html"
    context_object_name = "case"
    pk_url_kwarg = "case_id"

    def get_queryset(self):
        return (
            Case.objects.select_related("created_by", "reviewer", "analysis_result", "report")
            .prefetch_related(
                "audio_samples__original_artifact",
                "audio_samples__normalized_artifact",
                "audio_samples__cleaned_artifact",
                "audio_samples__feature_sets",
                "text_samples__feature_sets",
                "acoustic_feature_sets__sample",
                "linguistic_feature_sets__sample",
                "artifacts",
                "events__actor",
                "jobs",
                "report__versions__pdf_artifact",
            )
            .all()
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        case = self.object
        report_versions = list(case.report.versions.all()) if hasattr(case, "report") else []
        active_job = case.jobs.filter(is_current=True, status__in=ACTIVE_JOB_STATUSES).first()
        has_analysis = hasattr(case, "analysis_result")
        has_ready_report = hasattr(case, "report") and case.report.versions.filter(status=ReportVersion.Status.READY).exists()
        context["current_section"] = case.case_type
        context["page_heading"] = case.name
        context["page_subtitle"] = f"{case.case_number} | {case.get_case_type_display()} case workspace"
        context["review_form"] = ReviewDecisionForm(initial={"reviewer_notes": case.reviewer_notes})
        context["current_job"] = case.jobs.filter(is_current=True).first() or case.jobs.first()
        context["review_active_job"] = active_job
        context["review_analysis_ready"] = has_analysis
        context["review_report_ready"] = has_ready_report
        context["review_can_approve"] = (
            has_analysis
            and has_ready_report
            and active_job is None
            and case.status not in {Case.Status.COMPLETED, Case.Status.CANCELLED, Case.Status.FAILED}
        )
        context["review_can_refuse"] = (
            has_analysis
            and active_job is None
            and case.status not in {Case.Status.COMPLETED, Case.Status.CANCELLED, Case.Status.FAILED}
        )
        context["can_retry_case"] = (
            self.request.user.can_create_cases()
            and case.status == Case.Status.FAILED
            and not case.jobs.filter(is_current=True, status__in=ACTIVE_JOB_STATUSES).exists()
        )
        context["analysis_result"] = getattr(case, "analysis_result", None)
        context["audio_samples"] = case.audio_samples.all()
        context["text_samples"] = case.text_samples.all()
        context["report_versions"] = report_versions
        context["latest_report_version"] = report_versions[0] if report_versions else None
        context["acoustic_feature_sets"] = case.acoustic_feature_sets.select_related("sample")
        context["linguistic_feature_sets"] = case.linguistic_feature_sets.select_related("sample")
        context["original_artifacts"] = case.artifacts.filter(is_original=True)
        context["derived_artifacts"] = case.artifacts.filter(is_original=False)
        context["timeline_steps"] = [
            AnalysisJob.Stage.UPLOAD,
            AnalysisJob.Stage.QUEUED,
            AnalysisJob.Stage.PREPROCESSING,
            AnalysisJob.Stage.TRANSCRIPTION,
            AnalysisJob.Stage.FEATURE_EXTRACTION,
            AnalysisJob.Stage.COMPARISON,
            AnalysisJob.Stage.CALIBRATION,
            AnalysisJob.Stage.REPORT_DRAFTING,
            AnalysisJob.Stage.PDF_GENERATION,
            AnalysisJob.Stage.COMPLETED,
        ]
        context["current_stage_index"] = (
            context["timeline_steps"].index(case.current_stage) if case.current_stage in context["timeline_steps"] else -1
        )
        return context


class ReportsView(ForensicsTemplateView):
    template_name = "forensics/reports/index.html"
    current_section = "reports"
    page_heading = "Reports"
    page_subtitle = "Browse report versions, metadata, and secure downloads."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["reports"] = (
            Report.objects.select_related("case")
            .prefetch_related("versions__pdf_artifact")
            .order_by("-last_generated_at", "-created_at")
        )
        return context


class ReportDetailView(LoginRequiredMixin, DetailView):
    template_name = "forensics/reports/detail.html"
    context_object_name = "report"
    pk_url_kwarg = "report_id"

    def get_queryset(self):
        return Report.objects.select_related("case").prefetch_related("versions__pdf_artifact", "versions__generated_by_job")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["current_section"] = "reports"
        context["page_heading"] = f"Report {self.object.report_number}"
        context["page_subtitle"] = f"{self.object.case.case_number} | {self.object.case.name}"
        context["versions"] = list(self.object.versions.all())
        return context


class ReportDownloadView(LoginRequiredMixin, View):
    def get(self, request, version_id):
        version = get_object_or_404(ReportVersion.objects.select_related("report__case", "pdf_artifact"), pk=version_id)
        if not version.pdf_artifact:
            raise Http404("PDF artifact is not available.")
        log_event(
            event_type="download",
            title="Report downloaded",
            message=f"Downloaded report {version.report.report_number} version {version.version}.",
            case=version.report.case,
            actor=request.user,
            details={"report_version_id": str(version.id)},
        )
        response = FileResponse(version.pdf_artifact.file.open("rb"), as_attachment=True, filename=version.pdf_filename)
        response["Content-Type"] = version.pdf_artifact.mime_type
        return response


class ArtifactDownloadView(LoginRequiredMixin, View):
    def get(self, request, artifact_id):
        artifact = get_object_or_404(EvidenceArtifact.objects.select_related("case"), pk=artifact_id)
        log_event(
            event_type="download",
            title="Evidence artifact downloaded",
            message=f"Downloaded artifact {artifact.original_filename}.",
            case=artifact.case,
            actor=request.user,
            details={"artifact_id": str(artifact.id), "artifact_type": artifact.artifact_type},
        )
        response = FileResponse(artifact.file.open("rb"), as_attachment=True, filename=artifact.original_filename)
        response["Content-Type"] = artifact.mime_type
        return response


class CaseStatusPartialView(LoginRequiredMixin, DetailView):
    template_name = "forensics/partials/case_status.html"
    context_object_name = "case"
    pk_url_kwarg = "case_id"
    queryset = Case.objects.select_related("analysis_result", "report").prefetch_related("jobs")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["current_job"] = self.object.jobs.filter(is_current=True).first() or self.object.jobs.first()
        return context


class CaseTimelinePartialView(LoginRequiredMixin, DetailView):
    template_name = "forensics/partials/case_timeline.html"
    context_object_name = "case"
    pk_url_kwarg = "case_id"
    queryset = Case.objects.prefetch_related("jobs", "events__actor")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["timeline_steps"] = [
            AnalysisJob.Stage.UPLOAD,
            AnalysisJob.Stage.QUEUED,
            AnalysisJob.Stage.PREPROCESSING,
            AnalysisJob.Stage.TRANSCRIPTION,
            AnalysisJob.Stage.FEATURE_EXTRACTION,
            AnalysisJob.Stage.COMPARISON,
            AnalysisJob.Stage.CALIBRATION,
            AnalysisJob.Stage.REPORT_DRAFTING,
            AnalysisJob.Stage.PDF_GENERATION,
            AnalysisJob.Stage.COMPLETED,
        ]
        context["current_stage_index"] = (
            context["timeline_steps"].index(self.object.current_stage)
            if self.object.current_stage in context["timeline_steps"]
            else -1
        )
        return context


class CaseAnalysisPartialView(LoginRequiredMixin, DetailView):
    template_name = "forensics/partials/case_analysis_details.html"
    context_object_name = "case"
    pk_url_kwarg = "case_id"
    queryset = (
        Case.objects.select_related("analysis_result")
        .prefetch_related(
            "audio_samples",
            "acoustic_feature_sets__sample",
            "linguistic_feature_sets__sample",
        )
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["analysis_result"] = getattr(self.object, "analysis_result", None)
        context["audio_samples"] = self.object.audio_samples.all()
        context["acoustic_feature_sets"] = self.object.acoustic_feature_sets.select_related("sample")
        context["linguistic_feature_sets"] = self.object.linguistic_feature_sets.select_related("sample")
        return context


class ReportVersionsPartialView(LoginRequiredMixin, DetailView):
    template_name = "forensics/partials/report_versions.html"
    context_object_name = "case"
    pk_url_kwarg = "case_id"
    queryset = Case.objects.select_related("report").prefetch_related("report__versions__pdf_artifact")


class ReviewDecisionView(ReviewerRequiredMixin, View):
    def post(self, request, case_id):
        case = get_object_or_404(Case.objects.select_related("report").prefetch_related("report__versions", "jobs"), pk=case_id)

        form = ReviewDecisionForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Reviewer notes could not be saved.")
            return redirect("case_detail", case_id=case_id)

        decision = request.POST.get("decision")
        case.reviewer = request.user
        case.reviewer_notes = form.cleaned_data["reviewer_notes"]
        update_fields = ["reviewer", "reviewer_notes", "updated_at"]

        active_job = case.jobs.filter(is_current=True, status__in=ACTIVE_JOB_STATUSES).first()
        has_analysis = hasattr(case, "analysis_result")
        has_ready_report = hasattr(case, "report") and case.report.versions.filter(status=ReportVersion.Status.READY).exists()

        if decision == "save_notes":
            case.save(update_fields=update_fields)
            log_event(
                event_type="case",
                title="Reviewer notes saved",
                message=f"Reviewer notes were saved for case {case.case_number}.",
                case=case,
                actor=request.user,
                details={"decision": "save_notes"},
            )
            messages.success(request, "Reviewer notes saved.")
            return redirect("case_detail", case_id=case_id)

        if active_job:
            case.save(update_fields=update_fields)
            messages.error(request, "Reviewer notes were saved, but final review is locked while a current job is running.")
            return redirect("case_detail", case_id=case_id)

        if not has_analysis:
            case.save(update_fields=update_fields)
            messages.error(request, "Reviewer notes were saved, but final review requires completed structured analysis.")
            return redirect("case_detail", case_id=case_id)

        if case.status == Case.Status.COMPLETED:
            case.save(update_fields=update_fields)
            messages.error(request, "Reviewer notes were saved, but a final approved case is locked from further disposition changes.")
            return redirect("case_detail", case_id=case_id)

        if case.status in {Case.Status.CANCELLED, Case.Status.FAILED}:
            case.save(update_fields=update_fields)
            messages.error(request, "Reviewer notes were saved, but cancelled or failed cases cannot receive a final decision.")
            return redirect("case_detail", case_id=case_id)

        if decision == "approve":
            if not has_ready_report:
                case.save(update_fields=update_fields)
                messages.error(request, "Reviewer notes were saved, but approval requires a completed report PDF version.")
                return redirect("case_detail", case_id=case_id)
            case.reviewer_status = Case.ReviewerStatus.APPROVED
            case.status = Case.Status.COMPLETED
            update_fields.extend(["reviewer_status", "status"])
            messages.success(request, "Case approved as the final reviewed record.")
        elif decision == "reject":
            case.reviewer_status = Case.ReviewerStatus.REJECTED
            case.status = Case.Status.AWAITING_REVIEW
            update_fields.extend(["reviewer_status", "status"])
            messages.warning(request, "Case refused and returned for corrective action.")
        else:
            messages.error(request, "Unknown review decision.")
            return redirect("case_detail", case_id=case_id)

        case.save(update_fields=update_fields)
        log_event(
            event_type="case",
            title="Reviewer decision recorded",
            message=f"Reviewer {'approved' if decision == 'approve' else 'refused'} case {case.case_number}.",
            case=case,
            actor=request.user,
            details={"decision": decision},
        )
        return redirect("case_detail", case_id=case_id)


class CancelCaseView(CaseCreateRequiredMixin, View):
    def post(self, request, case_id):
        case = get_object_or_404(Case, pk=case_id)
        try:
            cancel_case(case, request.user)
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.warning(request, "Case cancelled.")
        return redirect("case_detail", case_id=case_id)


class RetryCaseView(CaseCreateRequiredMixin, View):
    def post(self, request, case_id):
        case = get_object_or_404(Case, pk=case_id)
        try:
            retry_case_analysis(case, request.user)
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Case analysis requeued.")
        return redirect("case_detail", case_id=case_id)


class RegenerateReportView(CaseCreateRequiredMixin, View):
    def post(self, request, case_id):
        case = get_object_or_404(Case, pk=case_id)
        if not hasattr(case, "analysis_result"):
            messages.error(request, "A report cannot be regenerated until analysis results exist.")
            return redirect("case_detail", case_id=case_id)

        current_report_job = case.jobs.filter(
            is_current=True,
            job_type=AnalysisJob.JobType.REPORT,
            status__in=[AnalysisJob.Status.PENDING, AnalysisJob.Status.RUNNING, AnalysisJob.Status.RETRYING],
        ).first()
        if current_report_job:
            messages.warning(request, "A report regeneration job is already running for this case.")
            return redirect("case_detail", case_id=case_id)

        from forensics.tasks import generate_report_version

        with transaction.atomic():
            job = AnalysisJob.objects.create(
                case=case,
                job_type=AnalysisJob.JobType.REPORT,
                status=AnalysisJob.Status.PENDING,
                stage=AnalysisJob.Stage.REPORT_DRAFTING,
                progress_percentage=0,
                metadata={"manual_regeneration": True},
            )
            case.current_stage = AnalysisJob.Stage.REPORT_DRAFTING
            case.task_state = "PENDING"
            case.failure_reason = ""
            case.save(update_fields=["current_stage", "task_state", "failure_reason", "updated_at"])
            queue_analysis_job(case=case, job=job, task=generate_report_version, queue_case_status=None)

        messages.success(request, "Report regeneration queued.")
        return redirect("case_detail", case_id=case_id)


class DashboardOperationalPartialView(LoginRequiredMixin, TemplateView):
    template_name = "forensics/partials/dashboard_operational.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["operational_summary"] = operational_summary()
        return context


class DashboardIntegrityPartialView(LoginRequiredMixin, TemplateView):
    template_name = "forensics/partials/dashboard_integrity.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["integrity_summary"] = integrity_summary()
        return context
