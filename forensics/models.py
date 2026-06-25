import uuid
from pathlib import Path

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from .storage import private_artifact_storage


def artifact_upload_to(instance: "EvidenceArtifact", filename: str) -> str:
    safe_name = Path(filename).name
    return (
        f"cases/{instance.case.case_number}/{instance.artifact_type}/"
        f"{timezone.now():%Y/%m/%d}/{uuid.uuid4()}-{safe_name}"
    )


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Case(TimeStampedModel):
    class CaseType(models.TextChoices):
        PHONETIC = "phonetic", "Phonetic"
        LINGUISTIC = "linguistic", "Linguistic"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        AWAITING_REVIEW = "awaiting_review", "Awaiting Review"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    class ReviewerStatus(models.TextChoices):
        NOT_REVIEWED = "not_reviewed", "Not Reviewed"
        APPROVED = "approved", "Reviewer Approved"
        REJECTED = "rejected", "Reviewer Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case_number = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    case_type = models.CharField(max_length=16, choices=CaseType.choices)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.DRAFT)
    reviewer_status = models.CharField(
        max_length=32,
        choices=ReviewerStatus.choices,
        default=ReviewerStatus.NOT_REVIEWED,
    )
    progress_percentage = models.PositiveSmallIntegerField(default=0)
    current_stage = models.CharField(max_length=64, default="upload")
    celery_task_id = models.CharField(max_length=255, blank=True)
    task_state = models.CharField(max_length=64, blank=True)
    failure_reason = models.TextField(blank=True)
    adverse_condition_flag = models.BooleanField(default=False)
    adverse_condition_warnings = models.JSONField(default=list, blank=True)
    preprocessing_notes = models.JSONField(default=list, blank=True)
    noise_removal_applied = models.BooleanField(default=False)
    integrity_warning = models.BooleanField(default=False)
    final_decision_label = models.CharField(max_length=128, blank=True)
    calibrated_score = models.FloatField(null=True, blank=True)
    evidential_strength = models.CharField(max_length=128, blank=True)
    model_versions = models.JSONField(default=dict, blank=True)
    feature_versions = models.JSONField(default=dict, blank=True)
    calibration_metadata = models.JSONField(default=dict, blank=True)
    validation_metadata = models.JSONField(default=dict, blank=True)
    detected_language = models.CharField(max_length=64, blank=True)
    analysis_started_at = models.DateTimeField(null=True, blank=True)
    analysis_completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_cases",
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="review_cases",
    )
    analyst_notes = models.TextField(blank=True)
    reviewer_notes = models.TextField(blank=True)
    legacy_analysis_case_id = models.BigIntegerField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ("-updated_at", "-created_at")

    def __str__(self) -> str:
        return f"{self.case_number} - {self.name}"

    @property
    def is_analyzed(self) -> bool:
        return self.status in {
            self.Status.AWAITING_REVIEW,
            self.Status.COMPLETED,
        } and hasattr(self, "analysis_result")

    @property
    def report_version_count(self) -> int:
        if not hasattr(self, "report"):
            return 0
        return self.report.versions.count()

    @property
    def status_badge_class(self) -> str:
        return {
            self.Status.DRAFT: "bg-secondary-subtle text-secondary",
            self.Status.QUEUED: "bg-warning-subtle text-warning",
            self.Status.RUNNING: "bg-info-subtle text-info",
            self.Status.AWAITING_REVIEW: "bg-primary-subtle text-primary",
            self.Status.COMPLETED: "bg-success-subtle text-success",
            self.Status.FAILED: "bg-danger-subtle text-danger",
            self.Status.CANCELLED: "bg-dark-subtle text-dark",
        }.get(self.status, "bg-secondary-subtle text-secondary")

    @property
    def reviewer_badge_class(self) -> str:
        return {
            self.ReviewerStatus.NOT_REVIEWED: "bg-secondary-subtle text-secondary",
            self.ReviewerStatus.APPROVED: "bg-success-subtle text-success",
            self.ReviewerStatus.REJECTED: "bg-danger-subtle text-danger",
        }.get(self.reviewer_status, "bg-secondary-subtle text-secondary")

    @property
    def case_type_badge_class(self) -> str:
        return {
            self.CaseType.PHONETIC: "bg-primary-subtle text-primary",
            self.CaseType.LINGUISTIC: "bg-info-subtle text-info",
        }.get(self.case_type, "bg-secondary-subtle text-secondary")

    @property
    def current_stage_label(self) -> str:
        try:
            return AnalysisJob.Stage(self.current_stage).label
        except ValueError:
            return self.current_stage.replace("_", " ").title()

    @property
    def latest_report_version(self):
        if not hasattr(self, "report"):
            return None
        return self.report.versions.first()


class PhoneticCase(TimeStampedModel):
    case = models.OneToOneField(Case, on_delete=models.CASCADE, related_name="phonetic_case")
    language = models.CharField(max_length=64, blank=True)
    transcription_uncertainty = models.FloatField(null=True, blank=True)
    sample_a_duration_seconds = models.FloatField(null=True, blank=True)
    sample_b_duration_seconds = models.FloatField(null=True, blank=True)
    comparison_summary = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        return f"Phonetic - {self.case}"


class LinguisticCase(TimeStampedModel):
    case = models.OneToOneField(
        Case,
        on_delete=models.CASCADE,
        related_name="linguistic_case",
    )
    language = models.CharField(max_length=64, blank=True)
    text_length_warning = models.BooleanField(default=False)
    topic_mismatch_score = models.FloatField(null=True, blank=True)
    comparison_summary = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        return f"Linguistic - {self.case}"


class EvidenceArtifact(TimeStampedModel):
    class ArtifactType(models.TextChoices):
        ORIGINAL_AUDIO = "original_audio", "Original Audio"
        NORMALIZED_AUDIO = "normalized_audio", "Normalized Audio"
        CLEANED_AUDIO = "cleaned_audio", "Cleaned Audio"
        WAVEFORM_PLOT = "waveform_plot", "Waveform Plot"
        SPECTROGRAM_PLOT = "spectrogram_plot", "Spectrogram Plot"
        FEATURE_EXPORT = "feature_export", "Feature Export"
        TRANSCRIPT_EXPORT = "transcript_export", "Transcript Export"
        REPORT_HTML = "report_html", "Report HTML"
        REPORT_PDF = "report_pdf", "Report PDF"
        JOB_LOG = "job_log", "Job Log"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="artifacts")
    artifact_type = models.CharField(max_length=32, choices=ArtifactType.choices)
    role = models.CharField(max_length=32, blank=True)
    file = models.FileField(storage=private_artifact_storage, upload_to=artifact_upload_to)
    original_filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=128)
    sha256 = models.CharField(max_length=64)
    file_size_bytes = models.BigIntegerField(default=0)
    immutable = models.BooleanField(default=True)
    is_original = models.BooleanField(default=False)
    processing_steps = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    derived_from = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="derivatives",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_artifacts",
    )

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.case.case_number} - {self.get_artifact_type_display()}"


class UploadedAudioSample(TimeStampedModel):
    class SampleRole(models.TextChoices):
        SAMPLE_A = "sample_a", "Sample A"
        SAMPLE_B = "sample_b", "Sample B"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="audio_samples")
    role = models.CharField(max_length=16, choices=SampleRole.choices)
    original_artifact = models.OneToOneField(
        EvidenceArtifact,
        on_delete=models.PROTECT,
        related_name="audio_original_for",
    )
    normalized_artifact = models.OneToOneField(
        EvidenceArtifact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audio_normalized_for",
    )
    cleaned_artifact = models.OneToOneField(
        EvidenceArtifact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audio_cleaned_for",
    )
    mime_type = models.CharField(max_length=128)
    extension = models.CharField(max_length=16)
    duration_seconds = models.FloatField(null=True, blank=True)
    sample_rate = models.PositiveIntegerField(null=True, blank=True)
    channels = models.PositiveSmallIntegerField(null=True, blank=True)
    detected_language = models.CharField(max_length=64, blank=True)
    transcript_text = models.TextField(blank=True)
    transcript_confidence = models.FloatField(null=True, blank=True)
    spoken_keywords = models.JSONField(default=list, blank=True)
    noise_detected = models.BooleanField(default=False)
    noise_removal_applied = models.BooleanField(default=False)
    quality_metrics = models.JSONField(default=dict, blank=True)
    preprocessing_steps = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ("role", "created_at")
        unique_together = (("case", "role"),)

    def __str__(self) -> str:
        return f"{self.case.case_number} - {self.get_role_display()}"


class TextSample(TimeStampedModel):
    class SampleRole(models.TextChoices):
        SUSPECTED = "suspected", "Suspected Sample"
        PROVIDED = "provided", "Provided Sample"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="text_samples")
    role = models.CharField(max_length=16, choices=SampleRole.choices)
    raw_text = models.TextField()
    normalized_text = models.TextField(blank=True)
    sha256 = models.CharField(max_length=64)
    detected_language = models.CharField(max_length=64, blank=True)
    text_length = models.PositiveIntegerField(default=0)
    token_count = models.PositiveIntegerField(default=0)
    sentence_count = models.PositiveIntegerField(default=0)
    encoding_warnings = models.JSONField(default=list, blank=True)
    preprocessing_steps = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ("role", "created_at")
        unique_together = (("case", "role"),)

    def __str__(self) -> str:
        return f"{self.case.case_number} - {self.get_role_display()}"


class AnalysisJob(TimeStampedModel):
    class JobType(models.TextChoices):
        PHONETIC = "phonetic", "Phonetic Analysis"
        LINGUISTIC = "linguistic", "Linguistic Analysis"
        REPORT = "report", "Report Generation"
        VALIDATION = "validation", "Validation"
        MAINTENANCE = "maintenance", "Maintenance"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        RETRYING = "retrying", "Retrying"

    class Stage(models.TextChoices):
        UPLOAD = "upload", "Upload"
        QUEUED = "queued", "Queued"
        PREPROCESSING = "preprocessing", "Preprocessing"
        TRANSCRIPTION = "transcription", "Transcription"
        FEATURE_EXTRACTION = "feature_extraction", "Feature Extraction"
        COMPARISON = "comparison", "Comparison"
        CALIBRATION = "calibration", "Calibration"
        REPORT_DRAFTING = "report_drafting", "Report Drafting"
        PDF_GENERATION = "pdf_generation", "PDF Generation"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="jobs")
    job_type = models.CharField(max_length=20, choices=JobType.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    stage = models.CharField(max_length=32, choices=Stage.choices, default=Stage.UPLOAD)
    progress_percentage = models.PositiveSmallIntegerField(default=0)
    celery_task_id = models.CharField(max_length=255, blank=True)
    task_state = models.CharField(max_length=64, blank=True)
    queue_name = models.CharField(max_length=64, default="analysis")
    retry_count = models.PositiveSmallIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    log_excerpt = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    cancel_requested = models.BooleanField(default=False)
    is_current = models.BooleanField(default=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.case.case_number} - {self.get_job_type_display()}"

    @property
    def status_badge_class(self) -> str:
        return {
            self.Status.PENDING: "bg-warning-subtle text-warning",
            self.Status.RUNNING: "bg-info-subtle text-info",
            self.Status.SUCCEEDED: "bg-success-subtle text-success",
            self.Status.FAILED: "bg-danger-subtle text-danger",
            self.Status.CANCELLED: "bg-dark-subtle text-dark",
            self.Status.RETRYING: "bg-primary-subtle text-primary",
        }.get(self.status, "bg-secondary-subtle text-secondary")

    @property
    def stage_label(self) -> str:
        try:
            return self.Stage(self.stage).label
        except ValueError:
            return self.stage.replace("_", " ").title()


class AnalysisResult(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.OneToOneField(Case, on_delete=models.CASCADE, related_name="analysis_result")
    raw_score = models.FloatField(null=True, blank=True)
    calibrated_score = models.FloatField(null=True, blank=True)
    conclusion_label = models.CharField(max_length=128, blank=True)
    evidence_summary = models.TextField(blank=True)
    evidence_payload = models.JSONField(default=dict, blank=True)
    methodology = models.TextField(blank=True)
    top_shared_markers = models.JSONField(default=list, blank=True)
    top_divergent_markers = models.JSONField(default=list, blank=True)
    comparison_metrics = models.JSONField(default=dict, blank=True)
    model_versions = models.JSONField(default=dict, blank=True)
    feature_versions = models.JSONField(default=dict, blank=True)
    calibration_metadata = models.JSONField(default=dict, blank=True)
    validation_metadata = models.JSONField(default=dict, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Result - {self.case.case_number}"


class AcousticFeatureSet(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="acoustic_feature_sets")
    sample = models.ForeignKey(
        UploadedAudioSample,
        on_delete=models.CASCADE,
        related_name="feature_sets",
    )
    role = models.CharField(max_length=16)
    f0_mean = models.FloatField(null=True, blank=True)
    f0_std = models.FloatField(null=True, blank=True)
    f1_mean = models.FloatField(null=True, blank=True)
    f2_mean = models.FloatField(null=True, blank=True)
    f3_mean = models.FloatField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    pause_count = models.PositiveIntegerField(default=0)
    pause_duration_seconds = models.FloatField(default=0.0)
    mean_intensity = models.FloatField(null=True, blank=True)
    energy = models.FloatField(null=True, blank=True)
    jitter_local = models.FloatField(null=True, blank=True)
    shimmer_local = models.FloatField(null=True, blank=True)
    hnr = models.FloatField(null=True, blank=True)
    mfcc_summary = models.JSONField(default=dict, blank=True)
    spectral_descriptors = models.JSONField(default=dict, blank=True)
    embedding_vector = models.JSONField(default=list, blank=True)
    detailed_metrics = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("role", "created_at")

    def __str__(self) -> str:
        return f"Acoustic {self.case.case_number} - {self.role}"


class LinguisticFeatureSet(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(
        Case,
        on_delete=models.CASCADE,
        related_name="linguistic_feature_sets",
    )
    sample = models.ForeignKey(
        TextSample,
        on_delete=models.CASCADE,
        related_name="feature_sets",
    )
    role = models.CharField(max_length=16)
    char_ngrams = models.JSONField(default=dict, blank=True)
    word_ngrams = models.JSONField(default=dict, blank=True)
    function_word_frequencies = models.JSONField(default=dict, blank=True)
    punctuation_profile = models.JSONField(default=dict, blank=True)
    capitalization_profile = models.JSONField(default=dict, blank=True)
    sentence_length_stats = models.JSONField(default=dict, blank=True)
    token_length_stats = models.JSONField(default=dict, blank=True)
    lexical_richness = models.JSONField(default=dict, blank=True)
    repeated_phrases = models.JSONField(default=list, blank=True)
    spelling_habits = models.JSONField(default=dict, blank=True)
    whitespace_patterns = models.JSONField(default=dict, blank=True)
    pos_patterns = models.JSONField(default=dict, blank=True)
    feature_contributions = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("role", "created_at")

    def __str__(self) -> str:
        return f"Linguistic {self.case.case_number} - {self.role}"


class Report(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.OneToOneField(Case, on_delete=models.CASCADE, related_name="report")
    report_number = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    latest_version_number = models.PositiveSmallIntegerField(default=0)
    last_generated_at = models.DateTimeField(null=True, blank=True)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_reports",
    )

    def __str__(self) -> str:
        return self.report_number


class ReportVersion(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    model_name = models.CharField(max_length=128, blank=True)
    prompt_text = models.TextField(blank=True)
    prompt_metadata = models.JSONField(default=dict, blank=True)
    evidence_snapshot = models.JSONField(default=dict, blank=True)
    rendered_html = models.TextField(blank=True)
    rendered_markdown = models.TextField(blank=True)
    pdf_artifact = models.OneToOneField(
        EvidenceArtifact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="report_version_pdf",
    )
    generated_by_job = models.ForeignKey(
        AnalysisJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_report_versions",
    )
    generated_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True)

    class Meta:
        ordering = ("-version", "-created_at")
        unique_together = (("report", "version"),)

    def __str__(self) -> str:
        return f"{self.report.report_number} v{self.version}"

    @property
    def pdf_filename(self) -> str:
        case_name = slugify(self.report.case.name) or self.report.case.case_number.lower()
        return f"{case_name}-{self.report.report_number}-v{self.version}.pdf"

    @property
    def status_badge_class(self) -> str:
        return {
            self.Status.PENDING: "bg-warning-subtle text-warning",
            self.Status.READY: "bg-success-subtle text-success",
            self.Status.FAILED: "bg-danger-subtle text-danger",
        }.get(self.status, "bg-secondary-subtle text-secondary")

    @property
    def response_id(self) -> str:
        return self.prompt_metadata.get("response", {}).get("response_id", "")

    @property
    def narrative_sections(self):
        from forensics.services.reporting import parse_report_sections

        return parse_report_sections(self.rendered_markdown)


class ModelVersion(TimeStampedModel):
    class Domain(models.TextChoices):
        PHONETIC = "phonetic", "Phonetic"
        LINGUISTIC = "linguistic", "Linguistic"
        REPORTING = "reporting", "Reporting"
        TRANSCRIPTION = "transcription", "Transcription"
        LANGUAGE_ID = "language_id", "Language Identification"

    class ValidationStatus(models.TextChoices):
        CURRENT = "current", "Current"
        STALE = "stale", "Stale"
        MISSING = "missing", "Missing"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    domain = models.CharField(max_length=32, choices=Domain.choices)
    name = models.CharField(max_length=128)
    version = models.CharField(max_length=128)
    checksum = models.CharField(max_length=128, blank=True)
    source = models.CharField(max_length=255, blank=True)
    active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    calibration_metadata = models.JSONField(default=dict, blank=True)
    benchmark_summary = models.JSONField(default=dict, blank=True)
    validation_status = models.CharField(
        max_length=16,
        choices=ValidationStatus.choices,
        default=ValidationStatus.MISSING,
    )
    last_validated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("domain", "-created_at")
        unique_together = (("domain", "name", "version"),)

    def __str__(self) -> str:
        return f"{self.domain}:{self.name}:{self.version}"


class ValidationRun(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    model_version = models.ForeignKey(
        ModelVersion,
        on_delete=models.CASCADE,
        related_name="validation_runs",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    benchmark_name = models.CharField(max_length=128)
    metrics = models.JSONField(default=dict, blank=True)
    summary = models.TextField(blank=True)
    ran_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.model_version} - {self.benchmark_name}"


class EventLog(TimeStampedModel):
    class EventType(models.TextChoices):
        CASE = "case", "Case"
        TASK = "task", "Task"
        REPORT = "report", "Report"
        INVITATION = "invitation", "Invitation"
        DOWNLOAD = "download", "Download"
        INTEGRITY = "integrity", "Integrity"
        VALIDATION = "validation", "Validation"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    case = models.ForeignKey(
        Case,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="forensic_events",
    )
    event_type = models.CharField(max_length=16, choices=EventType.choices)
    title = models.CharField(max_length=255)
    message = models.TextField()
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.title
