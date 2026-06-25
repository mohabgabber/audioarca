from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from forensics import tasks as forensic_tasks
from forensics.services import analysis as analysis_services
from forensics.forms import LinguisticCaseCreateForm, PhoneticCaseCreateForm
from forensics.models import AcousticFeatureSet, AnalysisJob, AnalysisResult, Case, LinguisticFeatureSet, Report, ReportVersion
from forensics.services.analysis import ModelAssetPermissionError, resolve_speechbrain_savedir
from forensics.services.cases import cancel_case, create_linguistic_case, create_phonetic_case, queue_analysis_job
from forensics.services.reporting import ensure_report_version, render_section_body_html, validate_report_numbers
from forensics.services.tracking import JobTracker, set_current_job
from forensics.storage import private_artifact_storage

TEST_ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
TEST_CSRF_TRUSTED_ORIGINS = ["http://testserver", "http://localhost", "http://127.0.0.1"]

REPORT_MARKDOWN = """# Executive Summary
Structured evidence supports a same-source explanation more strongly than a different-source explanation.

# Methodology
- Evidence was analyzed locally before report drafting.
- The narrative stays within the stored evidence package.

# Observations
Multiple shared indicators were observed across the submitted material.

# Interpretation
The calibrated score supports the same-source proposition.

# Limitations
Any preprocessing or denoising steps are disclosed explicitly.

# Reviewer Notes
Reviewer sign-off remains pending.
"""

PROMPT_METADATA = {
    "system_prompt": "Use only stored evidence.",
    "user_prompt": "Structured evidence payload.",
    "response": {"response_id": "resp_test_123", "model": "gpt-test"},
}


class ForensicsBaseTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        private_root = Path(self.temp_dir.name) / "private_media"
        private_root.mkdir(parents=True, exist_ok=True)
        private_artifact_storage.location = str(private_root)
        private_artifact_storage.base_location = str(private_root)
        private_artifact_storage.base_url = None

        self.user_model = get_user_model()
        self.admin = self.user_model.objects.create_user(
            email="admin@example.com",
            username="admin",
            first_name="Admin",
            last_name="User",
            password="AdminPass!123",
            role=self.user_model.Role.ADMIN,
        )
        self.analyst = self.user_model.objects.create_user(
            email="analyst@example.com",
            username="analyst",
            first_name="Analyst",
            last_name="User",
            password="AnalystPass!123",
            role=self.user_model.Role.ANALYST,
        )
        self.reviewer = self.user_model.objects.create_user(
            email="reviewer@example.com",
            username="reviewer",
            first_name="Review",
            last_name="User",
            password="ReviewerPass!123",
            role=self.user_model.Role.REVIEWER,
        )
        self.viewer = self.user_model.objects.create_user(
            email="viewer@example.com",
            username="viewer",
            first_name="View",
            last_name="User",
            password="ViewerPass!123",
            role=self.user_model.Role.VIEWER,
        )

    def _audio_file(self, name: str, *, content_type: str = "audio/wav", content: bytes | None = None) -> SimpleUploadedFile:
        return SimpleUploadedFile(name, content or b"RIFF....WAVEfmt ", content_type=content_type)

    def _write_mock_pdf(self, _html: str, output_path: Path) -> None:
        Path(output_path).write_bytes(b"%PDF-1.4 mock forensic report")

    def _phonetic_form(self, **overrides) -> PhoneticCaseCreateForm:
        form = PhoneticCaseCreateForm(
            data={
                "case_name": overrides.get("case_name", "Speaker Pair Alpha"),
                "case_number": overrides.get("case_number", "PHN-TST-001"),
                "description": overrides.get("description", "Two call intercept samples requiring comparison."),
            },
            files={
                "sample_a": overrides.get("sample_a", self._audio_file("sample-a.wav")),
                "sample_b": overrides.get("sample_b", self._audio_file("sample-b.wav")),
            },
        )
        self.assertTrue(form.is_valid(), form.errors)
        return form

    def _linguistic_form(self, **overrides) -> LinguisticCaseCreateForm:
        form = LinguisticCaseCreateForm(
            data={
                "case_name": overrides.get("case_name", "Email Authorship"),
                "case_number": overrides.get("case_number", "LNG-TST-001"),
                "description": overrides.get("description", "Authorship comparison between questioned and known text."),
                "suspected_sample_text": overrides.get(
                    "suspected_sample_text",
                    "This is a sufficiently long suspected text sample used for stylometric comparison. " * 2,
                ),
                "provided_sample_text": overrides.get(
                    "provided_sample_text",
                    "This is a sufficiently long provided text sample used for stylometric comparison. " * 2,
                ),
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        return form

    def _phonetic_evidence(self, case: Case) -> dict:
        sample_a, sample_b = case.audio_samples.order_by("role")
        return {
            "sample_a": {"sha256": sample_a.original_artifact.sha256, "language": "en", "transcript": sample_a.transcript_text},
            "sample_b": {"sha256": sample_b.original_artifact.sha256, "language": "en", "transcript": sample_b.transcript_text},
            "warnings": ["Low signal-to-noise ratio observed in Sample A."],
            "embedding_similarity": 0.87,
            "pitch_similarity": 0.91,
            "formant_similarity": 0.84,
        }

    def _linguistic_evidence(self, case: Case) -> dict:
        suspected, provided = case.text_samples.order_by("role")
        return {
            "suspected_sample": {"sha256": suspected.sha256, "language": "en", "token_count": suspected.token_count},
            "provided_sample": {"sha256": provided.sha256, "language": "en", "token_count": provided.token_count},
            "warnings": ["Topic drift caution recorded for contextual review."],
            "ngram_similarity": 0.74,
            "function_word_similarity": 0.79,
        }

    def _make_ready_phonetic_case(self, case_number: str = "PHN-READY-001") -> tuple[Case, AnalysisJob]:
        form = self._phonetic_form(case_number=case_number)
        with patch.object(forensic_tasks.run_phonetic_analysis, "delay", return_value=SimpleNamespace(id="queued-job")):
            with self.captureOnCommitCallbacks(execute=True):
                result = create_phonetic_case(user=self.analyst, cleaned_data=form.cleaned_data)

        for index, sample in enumerate(result.case.audio_samples.order_by("role"), start=1):
            sample.detected_language = "en"
            sample.transcript_text = f"Transcript for {sample.get_role_display().lower()}."
            sample.transcript_confidence = 0.87
            sample.spoken_keywords = ["speaker", "comparison", "sample"]
            sample.noise_detected = index == 1
            sample.noise_removal_applied = index == 1
            sample.duration_seconds = 4.0 + (index / 10)
            sample.sample_rate = 16000
            sample.channels = 1
            sample.save()
            AcousticFeatureSet.objects.create(
                case=result.case,
                sample=sample,
                role=sample.role,
                f0_mean=119.4 + index,
                f1_mean=515.0 + index,
                f2_mean=1498.0 + index,
                f3_mean=2502.0 + index,
                duration_seconds=sample.duration_seconds,
                pause_count=2,
                pause_duration_seconds=0.25,
                mean_intensity=63.2,
                energy=0.82,
                jitter_local=0.011,
                shimmer_local=0.024,
                hnr=18.6,
                mfcc_summary={"mean": [1.0, 2.0]},
                spectral_descriptors={"spectral_centroid": 3310.0},
                embedding_vector=[0.1, 0.2, 0.3],
                detailed_metrics={"opensmile": {"mock": True}},
            )

        result.case.detected_language = "en"
        result.case.adverse_condition_flag = True
        result.case.adverse_condition_warnings = ["Low signal-to-noise ratio observed in Sample A."]
        result.case.preprocessing_notes = ["Resampled to 16 kHz", "Applied denoising to Sample A"]
        result.case.noise_removal_applied = True
        result.case.final_decision_label = "Strongly supports same speaker"
        result.case.calibrated_score = 0.91
        result.case.evidential_strength = "Strong support"
        result.case.status = Case.Status.AWAITING_REVIEW
        result.case.current_stage = AnalysisJob.Stage.COMPLETED
        result.case.progress_percentage = 100
        result.case.save()
        AnalysisResult.objects.create(
            case=result.case,
            raw_score=0.83,
            calibrated_score=0.91,
            conclusion_label="Strongly supports same speaker",
            evidence_payload=self._phonetic_evidence(result.case),
            evidence_summary="Shared acoustic and lexical indicators were observed in both recordings.",
            methodology="Mocked phonetic workflow for verification.",
            comparison_metrics={"embedding_similarity": 0.87, "pitch_similarity": 0.91, "formant_similarity": 0.84},
            calibration_metadata={"parameters": {"offset": -0.25, "scale": 4.2}},
        )
        with patch("forensics.services.reporting.render_report_markdown", return_value=(REPORT_MARKDOWN, PROMPT_METADATA)):
            with patch("forensics.services.reporting.render_pdf", side_effect=self._write_mock_pdf):
                ensure_report_version(case=result.case, job=result.job)
        result.job.status = AnalysisJob.Status.SUCCEEDED
        result.job.stage = AnalysisJob.Stage.COMPLETED
        result.job.progress_percentage = 100
        result.job.task_state = "SUCCESS"
        result.job.save(update_fields=["status", "stage", "progress_percentage", "task_state", "updated_at"])
        return result.case, result.job


@override_settings(DEBUG=True, ALLOWED_HOSTS=TEST_ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS=TEST_CSRF_TRUSTED_ORIGINS)
class ForensicsWorkflowTests(ForensicsBaseTestCase):
    def test_viewer_can_create_cases_and_approve_ready_case(self):
        self.client.force_login(self.viewer)

        phonetic_response = self.client.get(reverse("phonetic_analysis"))
        linguistic_response = self.client.get(reverse("linguistic_analysis"))
        self.assertEqual(phonetic_response.status_code, 200)
        self.assertEqual(linguistic_response.status_code, 200)

        case, _job = self._make_ready_phonetic_case(case_number="PHN-VIEWER-REVIEW-001")
        response = self.client.post(
            reverse("case_review", kwargs={"case_id": case.id}),
            {"decision": "approve", "reviewer_notes": "Viewer account can confirm ready casework."},
        )

        case.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("case_detail", kwargs={"case_id": case.id}))
        self.assertEqual(case.status, Case.Status.COMPLETED)
        self.assertEqual(case.reviewer_status, Case.ReviewerStatus.APPROVED)
        self.assertEqual(case.reviewer, self.viewer)

    def test_phonetic_upload_rejects_invalid_extension_and_mime(self):
        self.client.force_login(self.analyst)
        extension_response = self.client.post(
            reverse("phonetic_analysis"),
            {
                "case_name": "Bad Extension",
                "description": "Bad extension",
                "sample_a": self._audio_file("sample-a.txt"),
                "sample_b": self._audio_file("sample-b.wav"),
            },
        )
        self.assertEqual(extension_response.status_code, 200)
        self.assertContains(extension_response, "Sample A must be a WAV or MP3 file.")

        mime_response = self.client.post(
            reverse("phonetic_analysis"),
            {
                "case_name": "Bad Mime",
                "description": "Bad MIME type",
                "sample_a": self._audio_file("sample-a.wav", content_type="text/plain", content=b"not-audio"),
                "sample_b": self._audio_file("sample-b.wav"),
            },
        )
        self.assertEqual(mime_response.status_code, 200)
        self.assertContains(mime_response, "unsupported MIME type")

    def test_case_creation_enqueues_only_after_commit(self):
        self.client.force_login(self.analyst)
        with patch.object(forensic_tasks.run_phonetic_analysis, "delay", return_value=SimpleNamespace(id="phonetic-job")) as mocked_delay:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                response = self.client.post(
                    reverse("phonetic_analysis"),
                    {
                        "case_name": "Speaker Pair Alpha",
                        "case_number": "PHN-QA-001",
                        "description": "Two call intercept samples requiring comparison.",
                        "sample_a": self._audio_file("sample-a.wav"),
                        "sample_b": self._audio_file("sample-b.wav"),
                    },
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

            case = Case.objects.get(case_number="PHN-QA-001")
            job = case.jobs.get(job_type=AnalysisJob.JobType.PHONETIC)
            self.assertEqual(response.status_code, 200)
            self.assertJSONEqual(
                response.content,
                {
                    "redirect_url": reverse("case_detail", kwargs={"case_id": str(case.id)}),
                    "case_id": str(case.id),
                },
            )
            self.assertEqual(len(callbacks), 1)
            mocked_delay.assert_not_called()
            self.assertEqual(case.audio_samples.count(), 2)
            self.assertEqual(case.artifacts.filter(is_original=True).count(), 2)
            callbacks[0]()

        case.refresh_from_db()
        job.refresh_from_db()
        mocked_delay.assert_called_once_with(str(case.id), str(job.id))
        self.assertEqual(case.task_state, "PENDING")
        self.assertEqual(job.task_state, "PENDING")
        self.assertEqual(job.celery_task_id, "phonetic-job")

    def test_linguistic_creation_hashes_text_and_redirects(self):
        self.client.force_login(self.analyst)
        with patch.object(forensic_tasks.run_linguistic_analysis, "delay", return_value=SimpleNamespace(id="ling-job")) as mocked_delay:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("linguistic_analysis"),
                    {
                        "case_name": "Email Authorship",
                        "case_number": "LNG-QA-001",
                        "description": "Authorship comparison for two email samples.",
                        "suspected_sample_text": "This is a sufficiently long suspected text sample used for stylometric comparison. " * 2,
                        "provided_sample_text": "This is a sufficiently long provided text sample used for stylometric comparison. " * 2,
                    },
                )

        case = Case.objects.get(case_number="LNG-QA-001")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("case_detail", kwargs={"case_id": case.id}))
        self.assertEqual(case.text_samples.count(), 2)
        self.assertTrue(all(sample.sha256 for sample in case.text_samples.all()))
        mocked_delay.assert_called_once()

    def test_linguistic_pipeline_handles_low_signal_text_without_vectorizer_crash(self):
        form = self._linguistic_form(
            case_number="LNG-EDGE-001",
            suspected_sample_text="!!! ??? ;;; ... " * 8,
            provided_sample_text="the and of in to the and of in to. " * 8,
        )
        with patch.object(forensic_tasks.run_linguistic_analysis, "delay", return_value=SimpleNamespace(id="queued-ling-job")):
            with self.captureOnCommitCallbacks(execute=True):
                creation = create_linguistic_case(user=self.analyst, cleaned_data=form.cleaned_data)

        with patch("forensics.services.analysis.ensure_report_version") as mocked_report:
            result = analysis_services.run_linguistic_pipeline(creation.case, creation.job)

        creation.case.refresh_from_db()
        creation.job.refresh_from_db()
        creation.case.linguistic_case.refresh_from_db()
        self.assertEqual(creation.case.status, Case.Status.AWAITING_REVIEW)
        self.assertEqual(creation.job.status, AnalysisJob.Status.SUCCEEDED)
        self.assertEqual(creation.case.linguistic_feature_sets.count(), 2)
        self.assertTrue(creation.case.linguistic_case.text_length_warning)
        self.assertIsNotNone(creation.case.linguistic_case.topic_mismatch_score)
        self.assertEqual(creation.case.linguistic_case.comparison_summary["conclusion_label"], result.conclusion_label)
        self.assertIn("topic_similarity", result.comparison_metrics)
        self.assertTrue(any("too little text" in warning for warning in creation.case.adverse_condition_warnings))
        mocked_report.assert_called_once_with(case=creation.case, job=creation.job)

    def test_linguistic_vectorizer_helpers_fall_back_to_native_profiles(self):
        class FailingVectorizer:
            def __init__(self, **_kwargs):
                pass

            def fit_transform(self, _texts):
                raise ValueError("empty vocabulary")

        with patch.object(analysis_services, "CountVectorizer", FailingVectorizer):
            self.assertEqual(analysis_services._top_ngrams("!!! ???", analyzer="word", ngram_range=(1, 2)), {})
            self.assertEqual(
                analysis_services._top_ngrams("Alpha beta alpha.", analyzer="word", ngram_range=(1, 1))["alpha"],
                2,
            )

        with (
            patch.object(analysis_services, "TfidfVectorizer", FailingVectorizer),
            patch.object(analysis_services, "cosine_similarity", object()),
        ):
            similarity = analysis_services._topic_similarity("Alpha beta alpha.", "Alpha gamma.")

        self.assertGreater(similarity, 0)

    def test_detect_language_handles_fasttext_numpy_two_copy_error(self):
        class NativeFastText:
            def __init__(self):
                self.seen_text = None

            def predict(self, text, k, threshold, on_unicode_error):
                self.seen_text = text
                return [(0.98, "__label__ar")]

        class FastTextModel:
            def __init__(self):
                self.f = NativeFastText()

            def predict(self, *_args, **_kwargs):
                raise ValueError("Unable to avoid copy while creating an array as requested.")

        model = FastTextModel()
        with patch.object(analysis_services, "get_fasttext_model", return_value=model):
            self.assertEqual(analysis_services.detect_language("Arabic\nsample text"), "ar")

        self.assertEqual(model.f.seen_text, "Arabic sample text\n")

    def test_speechbrain_savedir_falls_back_when_primary_cache_is_not_writable(self):
        model_root = Path(self.temp_dir.name) / "model_assets"
        primary = model_root / analysis_services.SPEECHBRAIN_CACHE_DIR
        primary.mkdir(parents=True)
        (primary / "hyperparams.yaml").write_text("root-owned cache", encoding="utf-8")

        def fake_access(path, _mode):
            return Path(path).name != "hyperparams.yaml"

        with override_settings(FORENSICS_MODEL_ROOT=model_root):
            with patch.object(analysis_services.os, "access", side_effect=fake_access):
                savedir = resolve_speechbrain_savedir()

        self.assertEqual(savedir, model_root / analysis_services.SPEECHBRAIN_RUNTIME_CACHE_DIR)
        self.assertTrue(savedir.exists())

    def test_speechbrain_savedir_falls_back_when_primary_hyperparams_cannot_be_statted(self):
        model_root = Path(self.temp_dir.name) / "model_assets_stat_denied"
        primary = model_root / analysis_services.SPEECHBRAIN_CACHE_DIR
        primary.mkdir(parents=True)
        original_exists = analysis_services.Path.exists

        def fake_exists(path):
            if path.name == "hyperparams.yaml" and path.parent.name == analysis_services.SPEECHBRAIN_CACHE_DIR:
                raise PermissionError("stat denied")
            return original_exists(path)

        with override_settings(FORENSICS_MODEL_ROOT=model_root):
            with patch.object(analysis_services.Path, "exists", fake_exists):
                savedir = resolve_speechbrain_savedir()

        self.assertEqual(savedir, model_root / analysis_services.SPEECHBRAIN_RUNTIME_CACHE_DIR)

    def test_speaker_model_retries_runtime_cache_when_speechbrain_raises_permission_error(self):
        model_root = Path(self.temp_dir.name) / "model_assets_speechbrain_denied"
        sentinel_model = object()
        analysis_services.get_speaker_model.cache_clear()

        def fake_from_hparams(*, source, savedir, run_opts):
            self.assertEqual(source, "speechbrain/test-model")
            self.assertEqual(run_opts, {"device": "cpu"})
            if savedir.endswith(analysis_services.SPEECHBRAIN_CACHE_DIR):
                raise PermissionError("[Errno 13] Permission denied: 'hyperparams.yaml'")
            return sentinel_model

        try:
            with override_settings(FORENSICS_MODEL_ROOT=model_root, FORENSICS_SPEECHBRAIN_SOURCE="speechbrain/test-model"):
                with patch.object(
                    analysis_services,
                    "EncoderClassifier",
                    SimpleNamespace(from_hparams=fake_from_hparams),
                ):
                    self.assertIs(analysis_services.get_speaker_model(), sentinel_model)
        finally:
            analysis_services.get_speaker_model.cache_clear()

    def test_speaker_embedding_accepts_single_tensor_load_audio_return(self):
        class FakeSignal:
            def __init__(self, shape):
                self.shape = shape

            def dim(self):
                return len(self.shape)

            def unsqueeze(self, index):
                self.assert_index = index
                return FakeSignal((1, *self.shape))

        class FakeEmbedding:
            def squeeze(self):
                return self

            def detach(self):
                return self

            def cpu(self):
                return self

            def numpy(self):
                return analysis_services.np.array([0.4, 0.5, 0.6])

        class FakeClassifier:
            signal_shape = None

            def load_audio(self, _path):
                return FakeSignal((3,))

            def encode_batch(self, signal):
                self.signal_shape = tuple(signal.shape)
                return FakeEmbedding()

        fake_classifier = FakeClassifier()
        with patch("forensics.services.analysis.get_speaker_model", return_value=fake_classifier):
            embedding = analysis_services.speaker_embedding(Path("sample.wav"))

        self.assertEqual(fake_classifier.signal_shape, (1, 3))
        self.assertEqual(len(embedding), 3)
        self.assertAlmostEqual(embedding[0], 0.4, places=5)

    def test_speaker_embedding_accepts_tuple_load_audio_return(self):
        class FakeSignal:
            def __init__(self, shape):
                self.shape = shape

            def dim(self):
                return len(self.shape)

            def unsqueeze(self, _index):
                return FakeSignal((1, *self.shape))

        class FakeEmbedding:
            def squeeze(self):
                return self

            def detach(self):
                return self

            def cpu(self):
                return self

            def numpy(self):
                return analysis_services.np.array([0.1, 0.2])

        class FakeClassifier:
            signal_shape = None

            def load_audio(self, _path):
                return FakeSignal((2,)), 16000

            def encode_batch(self, signal):
                self.signal_shape = tuple(signal.shape)
                return FakeEmbedding()

        fake_classifier = FakeClassifier()
        with patch("forensics.services.analysis.get_speaker_model", return_value=fake_classifier):
            embedding = analysis_services.speaker_embedding(Path("sample.wav"))

        self.assertEqual(fake_classifier.signal_shape, (1, 2))
        self.assertEqual(len(embedding), 2)
        self.assertAlmostEqual(embedding[1], 0.2, places=5)

    def test_sanitize_json_value_replaces_nan_and_inf_with_none(self):
        payload = {
            "jitter_local": float("nan"),
            "shimmer_local": float("inf"),
            "nested": {"energy": 0.2, "values": [1.0, float("-inf")]},
        }

        sanitized = analysis_services._sanitize_json_value(payload)

        self.assertIsNone(sanitized["jitter_local"])
        self.assertIsNone(sanitized["shimmer_local"])
        self.assertEqual(sanitized["nested"]["energy"], 0.2)
        self.assertIsNone(sanitized["nested"]["values"][1])

    def test_speechbrain_savedir_raises_clear_error_when_model_root_is_not_writable(self):
        model_root = Path(self.temp_dir.name) / "readonly_model_assets"
        model_root.mkdir()

        with override_settings(FORENSICS_MODEL_ROOT=model_root):
            with patch.object(analysis_services.os, "access", return_value=False):
                with self.assertRaisesMessage(ModelAssetPermissionError, "Model asset root is not writable"):
                    resolve_speechbrain_savedir()

    def test_model_asset_permission_failure_marks_task_failed_without_retry(self):
        case = Case.objects.create(
            case_number="PHN-ASSET-FAIL-001",
            name="Asset Failure",
            description="Model cache permission failure.",
            case_type=Case.CaseType.PHONETIC,
            status=Case.Status.QUEUED,
            current_stage=AnalysisJob.Stage.QUEUED,
            created_by=self.analyst,
        )
        job = AnalysisJob.objects.create(
            case=case,
            job_type=AnalysisJob.JobType.PHONETIC,
            status=AnalysisJob.Status.PENDING,
            stage=AnalysisJob.Stage.PREPROCESSING,
        )

        with patch("forensics.tasks.run_phonetic_pipeline", side_effect=ModelAssetPermissionError("bad model cache")):
            result = forensic_tasks.run_phonetic_analysis.apply(args=[str(case.id), str(job.id)], throw=True)

        case.refresh_from_db()
        job.refresh_from_db()
        self.assertIsNone(result.result)
        self.assertEqual(job.status, AnalysisJob.Status.FAILED)
        self.assertEqual(job.retry_count, 0)
        self.assertEqual(case.status, Case.Status.FAILED)
        self.assertEqual(case.task_state, "FAILURE")
        self.assertIn("bad model cache", case.failure_reason)

    def test_validate_report_numbers_accepts_evidence_tokens_embedded_in_strings(self):
        evidence = {
            "case_number": "PHN-06",
            "sample_a": {"sha256": "abc123def"},
            "version_tag": "v02",
            "calibrated_score": 0.91,
        }
        markdown = """# 1. Executive Summary
Case PHN-06 preserved SHA-256-linked identifiers and version v02.

# Methodology
The calibrated score remained at 0.91.
"""

        validate_report_numbers(markdown, evidence)

    def test_validate_report_numbers_rejects_invented_numeric_values(self):
        evidence = {"calibrated_score": 0.91, "case_number": "PHN-06"}

        with self.assertRaisesMessage(ValueError, "42"):
            validate_report_numbers("The calibrated score was 0.91 across 42 reviewed indicators.", evidence)

    def test_report_section_renderer_handles_common_markdown(self):
        html = render_section_body_html(
            """## Sample A
Sample A is identified by SHA-256:
`36d40a884d8c22fa034bc76a8ed965f222ca2f6202df901ed5b9ec3144a2ae10`

- **Quality** indicators were recorded.
1. Numbered observation was recorded.
"""
        )

        self.assertIn("<h3>Sample A</h3>", html)
        self.assertIn(
            "<code>36d40a884d8c22fa034bc76a8ed965f222ca2f6202df901ed5b9ec3144a2ae10</code>",
            html,
        )
        self.assertIn("<strong>Quality</strong>", html)
        self.assertIn("<ol><li>Numbered observation was recorded.</li></ol>", html)
        self.assertNotIn("## Sample A", html)
        self.assertNotIn("`36d40", html)

    def test_ensure_report_version_reuses_failed_version_for_same_job(self):
        case = Case.objects.create(
            case_number="PHN-REPORT-RETRY-001",
            name="Report Retry",
            description="Retrying report generation should reuse the same version row.",
            case_type=Case.CaseType.PHONETIC,
            status=Case.Status.RUNNING,
            created_by=self.analyst,
        )
        job = AnalysisJob.objects.create(
            case=case,
            job_type=AnalysisJob.JobType.PHONETIC,
            status=AnalysisJob.Status.RUNNING,
            stage=AnalysisJob.Stage.REPORT_DRAFTING,
        )
        AnalysisResult.objects.create(
            case=case,
            raw_score=0.84,
            calibrated_score=0.91,
            conclusion_label="Strong support",
            evidence_payload={"case_number": case.case_number, "calibrated_score": 0.91},
            evidence_summary="Structured evidence package.",
            methodology="Mocked reporting retry workflow.",
        )

        with patch("forensics.services.reporting.render_report_markdown", side_effect=ValueError("report validation failed")):
            with self.assertRaisesMessage(ValueError, "report validation failed"):
                ensure_report_version(case=case, job=job)

        report = Report.objects.get(case=case)
        failed_version = ReportVersion.objects.get(report=report)
        self.assertEqual(failed_version.version, 1)
        self.assertEqual(failed_version.status, ReportVersion.Status.FAILED)
        self.assertEqual(report.status, Report.Status.FAILED)

        with patch("forensics.services.reporting.render_report_markdown", return_value=(REPORT_MARKDOWN, PROMPT_METADATA)):
            with patch("forensics.services.reporting.render_pdf", side_effect=self._write_mock_pdf):
                retried_version = ensure_report_version(case=case, job=job)

        report.refresh_from_db()
        failed_version.refresh_from_db()
        self.assertEqual(ReportVersion.objects.filter(report=report).count(), 1)
        self.assertEqual(retried_version.pk, failed_version.pk)
        self.assertEqual(retried_version.version, 1)
        self.assertEqual(retried_version.status, ReportVersion.Status.READY)
        self.assertEqual(report.status, Report.Status.READY)
        self.assertEqual(report.latest_version_number, 1)
        self.assertIn("<tr><th>Case status</th><td>Awaiting Review</td></tr>", retried_version.rendered_html)
        self.assertIn("<p><strong>Report status</strong><br>Ready</p>", retried_version.rendered_html)
        self.assertNotIn("<tr><th>Case status</th><td>Running</td></tr>", retried_version.rendered_html)
        self.assertNotIn("<p><strong>Model</strong><br>Pending</p>", retried_version.rendered_html)

    def test_case_detail_and_htmx_partials_render_expected_sections(self):
        case, _job = self._make_ready_phonetic_case(case_number="PHN-DETAIL-001")
        self.client.force_login(self.viewer)

        detail_response = self.client.get(reverse("case_detail", kwargs={"case_id": case.id}))
        status_response = self.client.get(reverse("case_status_partial", kwargs={"case_id": case.id}))
        timeline_response = self.client.get(reverse("case_timeline_partial", kwargs={"case_id": case.id}))
        analysis_response = self.client.get(reverse("case_analysis_partial", kwargs={"case_id": case.id}))
        report_response = self.client.get(reverse("report_versions_partial", kwargs={"case_id": case.id}))

        self.assertContains(detail_response, "Evidence explorer")
        self.assertContains(detail_response, "Transcript and extracted features")
        self.assertContains(detail_response, "Warnings and disclosures")
        self.assertContains(detail_response, "Task progress and audit trail")
        self.assertContains(detail_response, "Download PDF")
        self.assertContains(detail_response, "Forensic review docket")
        self.assertContains(detail_response, "Approve final record")
        self.assertContains(detail_response, "Refuse for correction")
        self.assertContains(status_response, "Case status")
        self.assertContains(status_response, "Current stage")
        self.assertContains(status_response, "Completed")
        self.assertContains(timeline_response, "Current")
        self.assertContains(analysis_response, "Comparison metric")
        self.assertContains(report_response, "Version 1")

    def test_failed_case_retry_requeues_existing_evidence(self):
        form = self._phonetic_form(case_number="PHN-RETRY-001")
        with patch.object(forensic_tasks.run_phonetic_analysis, "delay", return_value=SimpleNamespace(id="initial-job")):
            with self.captureOnCommitCallbacks(execute=True):
                creation = create_phonetic_case(user=self.analyst, cleaned_data=form.cleaned_data)

        creation.case.status = Case.Status.FAILED
        creation.case.current_stage = AnalysisJob.Stage.FAILED
        creation.case.progress_percentage = 45
        creation.case.task_state = "FAILURE"
        creation.case.failure_reason = "bad model cache"
        creation.case.save()
        creation.job.status = AnalysisJob.Status.FAILED
        creation.job.stage = AnalysisJob.Stage.FAILED
        creation.job.task_state = "FAILURE"
        creation.job.save()

        self.client.force_login(self.analyst)
        with patch.object(forensic_tasks.run_phonetic_analysis, "delay", return_value=SimpleNamespace(id="retry-job")) as mocked_delay:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(reverse("case_retry", kwargs={"case_id": creation.case.id}))

        creation.case.refresh_from_db()
        retry_job = creation.case.jobs.filter(job_type=AnalysisJob.JobType.PHONETIC).order_by("-created_at").first()
        creation.job.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("case_detail", kwargs={"case_id": creation.case.id}))
        self.assertEqual(creation.case.status, Case.Status.QUEUED)
        self.assertEqual(creation.case.current_stage, AnalysisJob.Stage.QUEUED)
        self.assertEqual(creation.case.progress_percentage, 5)
        self.assertEqual(creation.case.failure_reason, "")
        self.assertEqual(creation.case.task_state, "PENDING")
        self.assertEqual(creation.case.celery_task_id, "retry-job")
        self.assertFalse(creation.job.is_current)
        self.assertTrue(retry_job.is_current)
        self.assertEqual(retry_job.celery_task_id, "retry-job")
        mocked_delay.assert_called_once_with(str(creation.case.id), str(retry_job.id))

    def test_report_download_and_regeneration_behaviour(self):
        case, _job = self._make_ready_phonetic_case(case_number="PHN-REPORT-001")
        latest_version = case.report.versions.first()

        self.client.force_login(self.viewer)
        download_response = self.client.get(reverse("report_download", kwargs={"version_id": latest_version.id}))
        self.assertEqual(download_response.status_code, 200)
        self.assertIn(latest_version.pdf_filename, download_response["Content-Disposition"])
        self.assertEqual(b"".join(download_response.streaming_content), b"%PDF-1.4 mock forensic report")

        self.client.force_login(self.analyst)
        with patch.object(forensic_tasks.generate_report_version, "delay", return_value=SimpleNamespace(id="report-job")) as mocked_delay:
            with self.captureOnCommitCallbacks(execute=True):
                regenerate_response = self.client.post(reverse("report_regenerate", kwargs={"case_id": case.id}))

        case.refresh_from_db()
        report_job = case.jobs.filter(job_type=AnalysisJob.JobType.REPORT).first()
        self.assertEqual(regenerate_response.status_code, 302)
        self.assertEqual(case.status, Case.Status.AWAITING_REVIEW)
        self.assertEqual(case.task_state, "PENDING")
        self.assertTrue(report_job.is_current)
        mocked_delay.assert_called_once_with(str(case.id), str(report_job.id))

    def test_reviewer_can_save_notes_before_analysis_completion(self):
        case = Case.objects.create(
            case_number="PHN-REVIEW-NOTES-001",
            name="Pre-analysis Review Notes",
            description="Reviewer records early chain-of-custody context.",
            case_type=Case.CaseType.PHONETIC,
            status=Case.Status.QUEUED,
            created_by=self.analyst,
        )

        self.client.force_login(self.reviewer)
        response = self.client.post(
            reverse("case_review", kwargs={"case_id": case.id}),
            {"decision": "save_notes", "reviewer_notes": "Confirm original media provenance before final sign-off."},
        )

        case.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(case.status, Case.Status.QUEUED)
        self.assertEqual(case.reviewer_status, Case.ReviewerStatus.NOT_REVIEWED)
        self.assertEqual(case.reviewer, self.reviewer)
        self.assertEqual(case.reviewer_notes, "Confirm original media provenance before final sign-off.")
        self.assertTrue(case.events.filter(title="Reviewer notes saved").exists())

    def test_reviewer_can_refuse_and_later_approve_ready_case_from_sidebar(self):
        case, _job = self._make_ready_phonetic_case(case_number="PHN-REVIEW-DECISION-001")
        self.client.force_login(self.reviewer)

        detail_response = self.client.get(reverse("case_detail", kwargs={"case_id": case.id}))
        self.assertContains(detail_response, "Forensic review docket")
        self.assertContains(detail_response, "Approve final record")
        self.assertContains(detail_response, "Refuse for correction")

        refuse_response = self.client.post(
            reverse("case_review", kwargs={"case_id": case.id}),
            {"decision": "reject", "reviewer_notes": "Report needs clearer disclosure language before certification."},
        )

        case.refresh_from_db()
        self.assertEqual(refuse_response.status_code, 302)
        self.assertEqual(case.status, Case.Status.AWAITING_REVIEW)
        self.assertEqual(case.reviewer_status, Case.ReviewerStatus.REJECTED)
        self.assertEqual(case.reviewer, self.reviewer)
        self.assertIn("clearer disclosure", case.reviewer_notes)

        approve_response = self.client.post(
            reverse("case_review", kwargs={"case_id": case.id}),
            {"decision": "approve", "reviewer_notes": "Corrections accepted. Final report is suitable for case file use."},
        )

        case.refresh_from_db()
        self.assertEqual(approve_response.status_code, 302)
        self.assertEqual(case.status, Case.Status.COMPLETED)
        self.assertEqual(case.reviewer_status, Case.ReviewerStatus.APPROVED)
        self.assertIn("Final report is suitable", case.reviewer_notes)
        self.assertTrue(case.events.filter(title="Reviewer decision recorded", details__decision="approve").exists())

        locked_response = self.client.post(
            reverse("case_review", kwargs={"case_id": case.id}),
            {"decision": "reject", "reviewer_notes": "Additional comment after final approval."},
        )

        case.refresh_from_db()
        self.assertEqual(locked_response.status_code, 302)
        self.assertEqual(case.status, Case.Status.COMPLETED)
        self.assertEqual(case.reviewer_status, Case.ReviewerStatus.APPROVED)
        self.assertEqual(case.reviewer_notes, "Additional comment after final approval.")

    def test_review_cancellation_and_tracking_rules(self):
        case, job = self._make_ready_phonetic_case(case_number="PHN-STATE-001")

        ready_for_review = Case.objects.create(
            case_number="PHN-REVIEW-READY-001",
            name="Review Prerequisite",
            description="Requires report before approval.",
            case_type=Case.CaseType.PHONETIC,
            status=Case.Status.AWAITING_REVIEW,
            created_by=self.admin,
        )
        AnalysisResult.objects.create(
            case=ready_for_review,
            evidence_payload={"sample_a": {"sha256": "a"}, "sample_b": {"sha256": "b"}},
        )

        self.client.force_login(self.reviewer)
        review_response = self.client.post(
            reverse("case_review", kwargs={"case_id": ready_for_review.id}),
            {"decision": "approve", "reviewer_notes": "Needs a completed report first."},
        )
        ready_for_review.refresh_from_db()
        self.assertEqual(review_response.status_code, 302)
        self.assertEqual(ready_for_review.status, Case.Status.AWAITING_REVIEW)
        self.assertEqual(ready_for_review.reviewer_status, Case.ReviewerStatus.NOT_REVIEWED)
        self.assertEqual(ready_for_review.reviewer_notes, "Needs a completed report first.")

        tracker = JobTracker(job)
        tracker.start(AnalysisJob.Stage.PREPROCESSING, "Started preprocessing.")
        tracker.update(AnalysisJob.Stage.FEATURE_EXTRACTION, 45, "Extracted features.")
        tracker.retry(stage=AnalysisJob.Stage.COMPARISON, message="Retrying comparison after transient failure.")
        tracker.fail(stage=AnalysisJob.Stage.FAILED, message="Comparison failed after final retry.")

        case.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(job.status, AnalysisJob.Status.FAILED)
        self.assertEqual(job.retry_count, 1)
        self.assertEqual(case.status, Case.Status.FAILED)
        self.assertEqual(case.progress_percentage, 45)

        with self.assertRaisesMessage(ValueError, "Only queued, running, or review-pending cases can be cancelled."):
            cancel_case(Case.objects.create(
                case_number="PHN-CANCEL-001",
                name="Completed Case",
                description="Cancellation boundary.",
                case_type=Case.CaseType.PHONETIC,
                status=Case.Status.COMPLETED,
                created_by=self.admin,
            ), self.admin)

    def test_current_job_rotation_queue_callback_and_task_apply(self):
        case, job = self._make_ready_phonetic_case(case_number="PHN-INTEGRATION-001")
        new_job = AnalysisJob.objects.create(case=case, job_type=AnalysisJob.JobType.REPORT, is_current=False)
        set_current_job(new_job)
        job.refresh_from_db()
        new_job.refresh_from_db()
        self.assertFalse(job.is_current)
        self.assertTrue(new_job.is_current)

        queue_case = Case.objects.create(
            case_number="PHN-QUEUE-001",
            name="Queue Case",
            description="Queueing boundary.",
            case_type=Case.CaseType.PHONETIC,
            status=Case.Status.DRAFT,
            created_by=self.admin,
        )
        queue_job = AnalysisJob.objects.create(case=queue_case, job_type=AnalysisJob.JobType.PHONETIC, status=AnalysisJob.Status.PENDING)
        fake_task = SimpleNamespace(delay=lambda *_args: SimpleNamespace(id="queued-task"))
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            queue_analysis_job(case=queue_case, job=queue_job, task=fake_task)
        self.assertEqual(len(callbacks), 1)
        callbacks[0]()
        queue_case.refresh_from_db()
        queue_job.refresh_from_db()
        self.assertEqual(queue_case.task_state, "PENDING")
        self.assertEqual(queue_job.celery_task_id, "queued-task")

        ling_form = self._linguistic_form(case_number="LNG-INTEGRATION-001")
        with patch.object(forensic_tasks.run_linguistic_analysis, "delay", return_value=SimpleNamespace(id="queued-ling-job")):
            with self.captureOnCommitCallbacks(execute=True):
                creation = create_linguistic_case(user=self.analyst, cleaned_data=ling_form.cleaned_data)

        def fake_linguistic_pipeline(case_obj: Case, job_obj: AnalysisJob) -> AnalysisResult:
            tracker = JobTracker(job_obj)
            tracker.start(AnalysisJob.Stage.PREPROCESSING, "Starting mocked linguistic normalization.")
            for sample in case_obj.text_samples.order_by("role"):
                sample.detected_language = "en"
                sample.sentence_count = 3
                sample.save()
                LinguisticFeatureSet.objects.create(
                    case=case_obj,
                    sample=sample,
                    role=sample.role,
                    char_ngrams={"th": 12},
                    word_ngrams={"forensic analysis": 2},
                    function_word_frequencies={"the": 8},
                    punctuation_profile={"commas": 4},
                    capitalization_profile={"upper_ratio": 0.04},
                    sentence_length_stats={"mean": 17.3},
                    token_length_stats={"mean": 4.9},
                    lexical_richness={"type_token_ratio": 0.66},
                    repeated_phrases=["forensic analysis"],
                    spelling_habits={"colour": 1},
                    whitespace_patterns={"double_space": 0},
                    pos_patterns={"noun_verb": 0.41},
                    feature_contributions={"function_words": 0.34},
                )
            case_obj.detected_language = "en"
            case_obj.status = Case.Status.AWAITING_REVIEW
            case_obj.current_stage = AnalysisJob.Stage.COMPLETED
            case_obj.progress_percentage = 100
            case_obj.save()
            result_obj = AnalysisResult.objects.create(
                case=case_obj,
                raw_score=0.69,
                calibrated_score=0.74,
                conclusion_label="Moderately supports same author",
                evidence_payload=self._linguistic_evidence(case_obj),
                evidence_summary="Shared stylometric signals were observed across both texts.",
                methodology="Mocked linguistic workflow for verification.",
                comparison_metrics={"ngram_similarity": 0.74, "function_word_similarity": 0.79},
                calibration_metadata={"parameters": {"offset": -0.25, "scale": 4.2}},
            )
            ensure_report_version(case=case_obj, job=job_obj)
            tracker.succeed(stage=AnalysisJob.Stage.COMPLETED, message="Linguistic analysis and report generation completed.")
            case_obj.status = Case.Status.AWAITING_REVIEW
            case_obj.save(update_fields=["status", "updated_at"])
            return result_obj

        with patch("forensics.tasks.run_linguistic_pipeline", side_effect=fake_linguistic_pipeline):
            with patch("forensics.services.reporting.render_report_markdown", return_value=(REPORT_MARKDOWN, PROMPT_METADATA)):
                with patch("forensics.services.reporting.render_pdf", side_effect=self._write_mock_pdf):
                    result = forensic_tasks.run_linguistic_analysis.apply(args=[str(creation.case.id), str(creation.job.id)], throw=True)

        creation.case.refresh_from_db()
        creation.job.refresh_from_db()
        self.assertEqual(result.result, str(creation.case.analysis_result.id))
        self.assertEqual(creation.case.status, Case.Status.AWAITING_REVIEW)
        self.assertEqual(creation.job.status, AnalysisJob.Status.SUCCEEDED)
        self.assertEqual(creation.case.report.versions.filter(status="ready").count(), 1)


@override_settings(DEBUG=True, ALLOWED_HOSTS=TEST_ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS=TEST_CSRF_TRUSTED_ORIGINS)
class ProductionConfigSmokeTests(ForensicsBaseTestCase):
    def test_non_debug_collectstatic_and_deploy_checks_use_filesystem_storage(self):
        static_root = Path(self.temp_dir.name) / "staticroot"
        with override_settings(
            DEBUG=False,
            APP_DOMAIN="qa.example.test",
            ALLOWED_HOSTS=["qa.example.test"],
            CSRF_TRUSTED_ORIGINS=["https://qa.example.test"],
            SECURE_HSTS_SECONDS=31536000,
            SECURE_HSTS_INCLUDE_SUBDOMAINS=True,
            SECURE_HSTS_PRELOAD=True,
            SECURE_SSL_REDIRECT=True,
            SESSION_COOKIE_SECURE=True,
            CSRF_COOKIE_SECURE=True,
            X_FRAME_OPTIONS="DENY",
            STATIC_ROOT=static_root,
            STORAGES={
                "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            },
        ):
            call_command("check", deploy=True, fail_level="ERROR")
            call_command("collectstatic", interactive=False, verbosity=0, clear=True)

        self.assertTrue((static_root / "dash" / "assets" / "css" / "app.css").exists())
