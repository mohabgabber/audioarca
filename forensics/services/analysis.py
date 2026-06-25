from __future__ import annotations

from collections import Counter
from functools import lru_cache
from math import exp, isfinite
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
import os
import re

import numpy as np

try:
    import fasttext
except ImportError:  # pragma: no cover - resolved in container
    fasttext = None

try:
    from faster_whisper import WhisperModel
except ImportError:  # pragma: no cover - resolved in container
    WhisperModel = None

try:
    import librosa
    import librosa.display
except ImportError:  # pragma: no cover - resolved in container
    librosa = None

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:  # pragma: no cover - resolved in container
    plt = None

try:
    import opensmile
except ImportError:  # pragma: no cover - resolved in container
    opensmile = None

try:
    import parselmouth
except ImportError:  # pragma: no cover - resolved in container
    parselmouth = None

try:
    from scipy.signal import wiener
except ImportError:  # pragma: no cover - resolved in container
    wiener = None

try:
    from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError:  # pragma: no cover - resolved in container
    CountVectorizer = None
    TfidfVectorizer = None
    cosine_similarity = None

try:
    import torch
    import torchaudio
except ImportError:  # pragma: no cover - resolved in container
    torch = None
    torchaudio = None

try:
    from speechbrain.inference.speaker import EncoderClassifier
except ImportError:  # pragma: no cover - resolved in container
    EncoderClassifier = None

from django.conf import settings
from django.utils import timezone

from forensics.models import (
    AcousticFeatureSet,
    AnalysisJob,
    AnalysisResult,
    Case,
    LinguisticFeatureSet,
    TextSample,
    UploadedAudioSample,
)
from forensics.services.helpers import persist_generated_file
from forensics.services.reporting import ensure_report_version
from forensics.services.tracking import JobTracker


class AnalysisCancelled(Exception):
    pass


class ModelAssetPermissionError(RuntimeError):
    pass


SPEECHBRAIN_CACHE_DIR = "speechbrain-ecapa"
SPEECHBRAIN_RUNTIME_CACHE_DIR = "speechbrain-ecapa-runtime"


FUNCTION_WORDS = {
    "the",
    "and",
    "to",
    "of",
    "in",
    "that",
    "it",
    "is",
    "was",
    "i",
    "you",
    "he",
    "she",
    "they",
    "we",
    "for",
    "on",
    "with",
    "as",
    "at",
    "by",
    "an",
    "be",
    "this",
    "from",
}

LETTER_TOKEN_PATTERN = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)


def _require_runtime_dependency(name: str, obj) -> None:
    if obj is None:
        raise RuntimeError(f"{name} is required for the local forensic analysis pipeline.")


def _can_write_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    if not path.is_dir() or not os.access(path, os.W_OK | os.X_OK):
        return False

    try:
        with NamedTemporaryFile(dir=path, prefix=".write-check-", delete=True):
            pass
    except OSError:
        return False

    return True


def _speechbrain_cache_is_usable(path: Path) -> bool:
    if not _can_write_directory(path):
        return False

    hyperparams_path = path / "hyperparams.yaml"
    try:
        hyperparams_exists = hyperparams_path.exists()
    except OSError:
        return False

    if hyperparams_exists and not os.access(hyperparams_path, os.R_OK | os.W_OK):
        return False

    return True


def resolve_speechbrain_savedir() -> Path:
    model_root = Path(settings.FORENSICS_MODEL_ROOT)
    if not _can_write_directory(model_root):
        raise ModelAssetPermissionError(
            f"Model asset root is not writable: {model_root}. "
            "Fix the model_assets volume ownership so the application user can write to it."
        )

    primary = model_root / SPEECHBRAIN_CACHE_DIR
    if _speechbrain_cache_is_usable(primary):
        return primary

    fallback = model_root / SPEECHBRAIN_RUNTIME_CACHE_DIR
    if _speechbrain_cache_is_usable(fallback):
        return fallback

    raise ModelAssetPermissionError(
        f"SpeechBrain model cache is not writable at {primary} or {fallback}. "
        "The Docker model_assets volume may contain root-owned files; fix ownership or recreate the volume."
    )


@lru_cache(maxsize=1)
def get_fasttext_model():
    if fasttext is None or not Path(settings.FORENSICS_FASTTEXT_MODEL_PATH).exists():
        return None
    return fasttext.load_model(str(settings.FORENSICS_FASTTEXT_MODEL_PATH))


@lru_cache(maxsize=1)
def get_whisper_model():
    _require_runtime_dependency("faster-whisper", WhisperModel)
    return WhisperModel(settings.FORENSICS_WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")


@lru_cache(maxsize=1)
def get_speaker_model():
    _require_runtime_dependency("speechbrain", EncoderClassifier)
    model_root = resolve_speechbrain_savedir()
    try:
        return EncoderClassifier.from_hparams(
            source=settings.FORENSICS_SPEECHBRAIN_SOURCE,
            savedir=str(model_root),
            run_opts={"device": "cpu"},
        )
    except PermissionError as initial_exc:
        permission_error = initial_exc
        fallback = Path(settings.FORENSICS_MODEL_ROOT) / SPEECHBRAIN_RUNTIME_CACHE_DIR
        if model_root != fallback and _speechbrain_cache_is_usable(fallback):
            try:
                return EncoderClassifier.from_hparams(
                    source=settings.FORENSICS_SPEECHBRAIN_SOURCE,
                    savedir=str(fallback),
                    run_opts={"device": "cpu"},
                )
            except PermissionError as fallback_exc:
                permission_error = fallback_exc

        raise ModelAssetPermissionError(
            f"SpeechBrain model cache is not accessible: {permission_error}. "
            "Fix the model_assets volume ownership or recreate it, then retry the case."
        ) from permission_error


@lru_cache(maxsize=1)
def get_smile():
    _require_runtime_dependency("opensmile", opensmile)
    return opensmile.Smile(
        feature_set=opensmile.FeatureSet.eGeMAPSv02,
        feature_level=opensmile.FeatureLevel.Functionals,
    )


def _check_cancel_requested(job: AnalysisJob) -> None:
    job.refresh_from_db(fields=["cancel_requested"])
    if job.cancel_requested:
        raise AnalysisCancelled("Job cancellation requested.")


def detect_language(text: str) -> str:
    model = get_fasttext_model()
    if model is None or not text.strip():
        return ""
    normalized_text = text.replace("\n", " ")
    try:
        labels, _ = model.predict(normalized_text, k=1)
    except ValueError as exc:
        if "Unable to avoid copy while creating an array" not in str(exc) or not hasattr(model, "f"):
            raise
        labels = _predict_fasttext_labels_without_numpy_copy(model, normalized_text)
    if not labels:
        return ""
    return labels[0].replace("__label__", "")


def _predict_fasttext_labels_without_numpy_copy(model, text: str) -> list[str]:
    predictions = model.f.predict(f"{text}\n", 1, 0.0, "strict")
    labels = []
    for prediction in predictions:
        if isinstance(prediction, str):
            labels.append(prediction)
            continue
        if not isinstance(prediction, (list, tuple)) or len(prediction) < 2:
            continue
        left, right = prediction[0], prediction[1]
        labels.append(left if isinstance(left, str) else right)
    return labels


def extract_keywords(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z']{3,}", text.lower())
    frequencies = Counter(token for token in tokens if token not in FUNCTION_WORDS)
    return [token for token, _ in frequencies.most_common(12)]


def _sanitize_json_value(value):
    if isinstance(value, dict):
        return {key: _sanitize_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        return value if isfinite(value) else None
    return value


def _safe_float(value, default=None):
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return numeric if isfinite(numeric) else default


def _load_audio(path: Path):
    _require_runtime_dependency("torch", torch)
    _require_runtime_dependency("torchaudio", torchaudio)
    waveform, sample_rate = torchaudio.load(str(path))
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)
    if sample_rate != 16000:
        waveform = torchaudio.functional.resample(waveform, sample_rate, 16000)
        sample_rate = 16000
    max_abs = torch.max(torch.abs(waveform))
    if max_abs > 0:
        waveform = waveform / max_abs.clamp(min=1e-6)
    return waveform, sample_rate


def _save_waveform_to_temp(waveform, sample_rate: int) -> Path:
    temp = NamedTemporaryFile(suffix=".wav", delete=False)
    temp.close()
    torchaudio.save(temp.name, waveform, sample_rate)
    return Path(temp.name)


def assess_audio_quality(waveform, sample_rate: int) -> dict:
    _require_runtime_dependency("librosa", librosa)
    samples = waveform.squeeze(0).numpy()
    duration = float(len(samples) / sample_rate)
    rms = float(np.sqrt(np.mean(samples**2))) if len(samples) else 0.0
    clipping_ratio = float(np.mean(np.abs(samples) >= 0.99)) if len(samples) else 0.0
    spectral_flatness = float(np.mean(librosa.feature.spectral_flatness(y=samples)))
    non_silent = librosa.effects.split(samples, top_db=25)
    speech_duration = float(sum((end - start) for start, end in non_silent) / sample_rate) if len(non_silent) else 0.0
    silence_ratio = float(max(duration - speech_duration, 0.0) / duration) if duration else 1.0

    warnings: list[str] = []
    if duration < 2.0:
        warnings.append("Low duration may reduce discriminative power.")
    if clipping_ratio > 0.02:
        warnings.append("Clipping detected in the signal.")
    if silence_ratio > 0.45:
        warnings.append("High silence ratio detected.")
    if spectral_flatness > 0.3:
        warnings.append("Elevated broadband noise detected.")
    if rms < 0.02:
        warnings.append("Low energy signal detected.")

    return _sanitize_json_value({
        "duration_seconds": duration,
        "rms": rms,
        "clipping_ratio": clipping_ratio,
        "spectral_flatness": spectral_flatness,
        "speech_duration_seconds": speech_duration,
        "silence_ratio": silence_ratio,
        "noise_detected": spectral_flatness > 0.3 or rms < 0.02,
        "warnings": warnings,
    })


def denoise_waveform(waveform):
    if wiener is None:
        return waveform
    cleaned = wiener(waveform.squeeze(0).numpy())
    cleaned = np.nan_to_num(cleaned)
    return torch.tensor(cleaned, dtype=waveform.dtype).unsqueeze(0)


def transcribe_audio(path: Path) -> dict:
    model = get_whisper_model()
    segments, info = model.transcribe(str(path), beam_size=1, vad_filter=True)
    items = list(segments)
    text = " ".join(item.text.strip() for item in items).strip()
    mean_probability = float(np.mean([item.avg_logprob for item in items])) if items else 0.0
    return {
        "text": text,
        "language": getattr(info, "language", "") or detect_language(text),
        "confidence": mean_probability,
        "segments": [
            {
                "start": item.start,
                "end": item.end,
                "text": item.text.strip(),
                "avg_logprob": item.avg_logprob,
            }
            for item in items
        ],
    }


def speaker_embedding(path: Path) -> list[float]:
    classifier = get_speaker_model()
    loaded_audio = classifier.load_audio(str(path))
    signal = loaded_audio[0] if isinstance(loaded_audio, (tuple, list)) else loaded_audio
    if signal.dim() == 1:
        signal = signal.unsqueeze(0)
    embedding = classifier.encode_batch(signal)
    vector = embedding.squeeze().detach().cpu().numpy()
    return vector.astype(float).tolist()


def cosine_similarity_score(vector_a: list[float], vector_b: list[float]) -> float:
    if cosine_similarity is None:
        vec_a = np.array(vector_a)
        vec_b = np.array(vector_b)
        denom = np.linalg.norm(vec_a) * np.linalg.norm(vec_b)
        return float(np.dot(vec_a, vec_b) / denom) if denom else 0.0
    return float(cosine_similarity([vector_a], [vector_b])[0][0])


def extract_acoustic_metrics(path: Path, waveform, sample_rate: int) -> dict:
    _require_runtime_dependency("parselmouth", parselmouth)
    _require_runtime_dependency("librosa", librosa)
    _require_runtime_dependency("torchaudio", torchaudio)

    sound = parselmouth.Sound(str(path))
    pitch = sound.to_pitch()
    pitch_values = pitch.selected_array["frequency"]
    pitch_values = pitch_values[pitch_values > 0]

    formant = sound.to_formant_burg()
    times = np.arange(0.0, sound.get_total_duration(), 0.05)
    f1_values = [formant.get_value_at_time(1, time) for time in times]
    f2_values = [formant.get_value_at_time(2, time) for time in times]
    f3_values = [formant.get_value_at_time(3, time) for time in times]
    f1_values = [value for value in f1_values if value and not np.isnan(value)]
    f2_values = [value for value in f2_values if value and not np.isnan(value)]
    f3_values = [value for value in f3_values if value and not np.isnan(value)]

    point_process = parselmouth.praat.call(sound, "To PointProcess (periodic, cc)", 75, 500)
    jitter_local = _safe_float(parselmouth.praat.call(point_process, "Get jitter (local)", 0, 0, 75, 500, 1.3))
    shimmer_local = _safe_float(
        parselmouth.praat.call([sound, point_process], "Get shimmer (local)", 0, 0, 75, 500, 1.3, 1.6)
    )
    harmonicity = sound.to_harmonicity_cc()
    harmonicity_values = harmonicity.values[harmonicity.values > -200]
    hnr = float(np.mean(harmonicity_values)) if harmonicity_values.size else 0.0
    intensity = sound.to_intensity()
    intensity_values = intensity.values.flatten()
    intensity_values = intensity_values[intensity_values > 0]

    samples = waveform.squeeze(0).numpy()
    mfcc = torchaudio.transforms.MFCC(sample_rate=sample_rate, n_mfcc=13)(waveform).squeeze(0)
    smile_frame = get_smile().process_file(str(path)).iloc[0].to_dict()
    spectral_centroid = float(np.mean(librosa.feature.spectral_centroid(y=samples, sr=sample_rate)))
    spectral_bandwidth = float(np.mean(librosa.feature.spectral_bandwidth(y=samples, sr=sample_rate)))
    spectral_rolloff = float(np.mean(librosa.feature.spectral_rolloff(y=samples, sr=sample_rate)))
    spectral_flatness = float(np.mean(librosa.feature.spectral_flatness(y=samples)))
    energy = float(np.sum(samples**2) / max(len(samples), 1))
    non_silent = librosa.effects.split(samples, top_db=25)
    pause_count = max(len(non_silent) - 1, 0)
    pause_duration = 0.0
    for index in range(1, len(non_silent)):
        pause_duration += max(non_silent[index][0] - non_silent[index - 1][1], 0) / sample_rate

    return _sanitize_json_value({
        "f0_mean": float(np.mean(pitch_values)) if pitch_values.size else 0.0,
        "f0_std": float(np.std(pitch_values)) if pitch_values.size else 0.0,
        "f1_mean": float(np.mean(f1_values)) if f1_values else 0.0,
        "f2_mean": float(np.mean(f2_values)) if f2_values else 0.0,
        "f3_mean": float(np.mean(f3_values)) if f3_values else 0.0,
        "duration_seconds": float(len(samples) / sample_rate),
        "pause_count": pause_count,
        "pause_duration_seconds": pause_duration,
        "mean_intensity": float(np.mean(intensity_values)) if intensity_values.size else 0.0,
        "energy": energy,
        "jitter_local": jitter_local,
        "shimmer_local": shimmer_local,
        "hnr": hnr,
        "mfcc_summary": {
            "mean": mfcc.mean(dim=1).tolist(),
            "std": mfcc.std(dim=1).tolist(),
        },
        "spectral_descriptors": {
            "spectral_centroid": spectral_centroid,
            "spectral_bandwidth": spectral_bandwidth,
            "spectral_rolloff": spectral_rolloff,
            "spectral_flatness": spectral_flatness,
        },
        "detailed_metrics": {
            "opensmile": smile_frame,
        },
    })


def render_audio_plots(*, case: Case, sample: UploadedAudioSample, source_path: Path):
    if plt is None or librosa is None:
        return None, None

    samples, sample_rate = librosa.load(str(source_path), sr=None, mono=True)
    waveform_artifact = None
    spectrogram_artifact = None
    with TemporaryDirectory() as temp_dir:
        waveform_path = Path(temp_dir) / f"{sample.role}-waveform.png"
        spectrogram_path = Path(temp_dir) / f"{sample.role}-spectrogram.png"

        fig, ax = plt.subplots(figsize=(10, 3))
        ax.plot(samples, linewidth=0.5, color="#2470aa")
        ax.set_title(f"{sample.get_role_display()} waveform")
        ax.set_xlabel("Samples")
        ax.set_ylabel("Amplitude")
        fig.tight_layout()
        fig.savefig(waveform_path, dpi=160)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(10, 3))
        spectrum = librosa.amplitude_to_db(np.abs(librosa.stft(samples)), ref=np.max)
        img = librosa.display.specshow(spectrum, sr=sample_rate, x_axis="time", y_axis="hz", ax=ax)
        ax.set_title(f"{sample.get_role_display()} spectrogram")
        fig.colorbar(img, ax=ax, format="%+2.0f dB")
        fig.tight_layout()
        fig.savefig(spectrogram_path, dpi=160)
        plt.close(fig)

        waveform_artifact = persist_generated_file(
            case=case,
            source_path=waveform_path,
            artifact_type="waveform_plot",
            role=sample.role,
            filename=waveform_path.name,
            mime_type="image/png",
            created_by=case.created_by,
        )
        spectrogram_artifact = persist_generated_file(
            case=case,
            source_path=spectrogram_path,
            artifact_type="spectrogram_plot",
            role=sample.role,
            filename=spectrogram_path.name,
            mime_type="image/png",
            created_by=case.created_by,
        )
    return waveform_artifact, spectrogram_artifact


def _normalize_similarity(value: float, scale: float) -> float:
    return max(0.0, 1.0 - min(abs(value) / scale, 1.0))


def _phonetic_conclusion(score: float) -> str:
    if score >= 0.85:
        return "Strongly supports same speaker"
    if score >= 0.65:
        return "Moderately supports same speaker"
    if score >= 0.45:
        return "Inconclusive"
    if score >= 0.25:
        return "Moderately supports different speaker"
    return "Strongly supports different speaker"


def _linguistic_conclusion(score: float) -> str:
    if score >= 0.85:
        return "Strongly supports same author"
    if score >= 0.65:
        return "Moderately supports same author"
    if score >= 0.45:
        return "Inconclusive"
    if score >= 0.25:
        return "Moderately supports different author"
    return "Strongly supports different author"


def _calibrate_score(raw_score: float, weights: dict | None = None) -> tuple[float, dict]:
    params = {"offset": -0.25, "scale": 4.2}
    if weights:
        params.update(weights)
    calibrated = 1.0 / (1.0 + exp(-((raw_score + params["offset"]) * params["scale"])))
    return calibrated, {"strategy": "logistic_fallback", "parameters": params}


def run_phonetic_pipeline(case: Case, job: AnalysisJob) -> AnalysisResult:
    tracker = JobTracker(job)
    tracker.start(AnalysisJob.Stage.PREPROCESSING, "Starting phonetic preprocessing and evidence normalization.")
    case.status = Case.Status.RUNNING
    case.save(update_fields=["status", "updated_at"])
    samples = list(case.audio_samples.select_related("original_artifact", "normalized_artifact", "cleaned_artifact").all())
    if len(samples) != 2:
        raise RuntimeError("Phonetic analysis requires exactly two uploaded audio samples.")

    processed_samples: list[dict] = []
    adverse_warnings: list[str] = []

    for index, sample in enumerate(samples, start=1):
        _check_cancel_requested(job)
        waveform, sample_rate = _load_audio(Path(sample.original_artifact.file.path))
        normalized_path = _save_waveform_to_temp(waveform, sample_rate)
        normalized_artifact = persist_generated_file(
            case=case,
            source_path=normalized_path,
            artifact_type="normalized_audio",
            role=sample.role,
            filename=f"{sample.role}-normalized.wav",
            mime_type="audio/wav",
            created_by=case.created_by,
            derived_from=sample.original_artifact,
            processing_steps=["mono", "16k-resample", "peak-normalize"],
        )
        sample.normalized_artifact = normalized_artifact
        sample.preprocessing_steps = ["mono", "16k-resample", "peak-normalize"]

        quality = assess_audio_quality(waveform, sample_rate)
        cleaned_path = normalized_path
        if quality["noise_detected"]:
            cleaned_waveform = denoise_waveform(waveform)
            cleaned_path = _save_waveform_to_temp(cleaned_waveform, sample_rate)
            cleaned_artifact = persist_generated_file(
                case=case,
                source_path=cleaned_path,
                artifact_type="cleaned_audio",
                role=sample.role,
                filename=f"{sample.role}-cleaned.wav",
                mime_type="audio/wav",
                created_by=case.created_by,
                derived_from=normalized_artifact,
                processing_steps=["wiener-denoise"],
            )
            sample.cleaned_artifact = cleaned_artifact
            sample.noise_removal_applied = True
            case.noise_removal_applied = True

        transcription = transcribe_audio(cleaned_path)
        language = transcription["language"] or detect_language(transcription["text"])
        sample.detected_language = language
        sample.transcript_text = transcription["text"]
        sample.transcript_confidence = transcription["confidence"]
        sample.spoken_keywords = extract_keywords(transcription["text"])
        sample.noise_detected = quality["noise_detected"]
        sample.mime_type = sample.original_artifact.mime_type
        sample.extension = Path(sample.original_artifact.original_filename).suffix.lower().lstrip(".")
        sample.duration_seconds = quality["duration_seconds"]
        sample.sample_rate = sample_rate
        sample.channels = 1
        sample.quality_metrics = quality
        sample.save()

        render_audio_plots(case=case, sample=sample, source_path=cleaned_path)
        metrics = extract_acoustic_metrics(cleaned_path, _load_audio(cleaned_path)[0], sample_rate)
        embedding = speaker_embedding(cleaned_path)
        metrics["embedding_vector"] = embedding
        AcousticFeatureSet.objects.update_or_create(
            case=case,
            sample=sample,
            defaults={
                "role": sample.role,
                "f0_mean": metrics["f0_mean"],
                "f0_std": metrics["f0_std"],
                "f1_mean": metrics["f1_mean"],
                "f2_mean": metrics["f2_mean"],
                "f3_mean": metrics["f3_mean"],
                "duration_seconds": metrics["duration_seconds"],
                "pause_count": metrics["pause_count"],
                "pause_duration_seconds": metrics["pause_duration_seconds"],
                "mean_intensity": metrics["mean_intensity"],
                "energy": metrics["energy"],
                "jitter_local": metrics["jitter_local"],
                "shimmer_local": metrics["shimmer_local"],
                "hnr": metrics["hnr"],
                "mfcc_summary": metrics["mfcc_summary"],
                "spectral_descriptors": metrics["spectral_descriptors"],
                "embedding_vector": metrics["embedding_vector"],
                "detailed_metrics": metrics["detailed_metrics"],
            },
        )
        processed_samples.append({"sample": sample, "quality": quality, "metrics": metrics, "embedding": embedding})
        adverse_warnings.extend(quality["warnings"])
        tracker.update(
            AnalysisJob.Stage.FEATURE_EXTRACTION,
            20 + (index * 20),
            f"Extracted transcription and acoustic features for {sample.get_role_display()}.",
            metadata={"last_sample": sample.role},
        )

    _check_cancel_requested(job)
    tracker.update(AnalysisJob.Stage.COMPARISON, 70, "Comparing phonetic evidence across both samples.")
    feature_a = processed_samples[0]["metrics"]
    feature_b = processed_samples[1]["metrics"]
    embedding_similarity = cosine_similarity_score(processed_samples[0]["embedding"], processed_samples[1]["embedding"])
    pitch_similarity = _normalize_similarity(feature_a["f0_mean"] - feature_b["f0_mean"], 120.0)
    formant_similarity = np.mean(
        [
            _normalize_similarity(feature_a["f1_mean"] - feature_b["f1_mean"], 400.0),
            _normalize_similarity(feature_a["f2_mean"] - feature_b["f2_mean"], 500.0),
            _normalize_similarity(feature_a["f3_mean"] - feature_b["f3_mean"], 700.0),
        ]
    )
    rhythm_similarity = np.mean(
        [
            _normalize_similarity(feature_a["pause_duration_seconds"] - feature_b["pause_duration_seconds"], 2.0),
            _normalize_similarity(feature_a["duration_seconds"] - feature_b["duration_seconds"], 6.0),
        ]
    )
    lexical_overlap = len(
        set(processed_samples[0]["sample"].spoken_keywords).intersection(processed_samples[1]["sample"].spoken_keywords)
    ) / max(
        len(set(processed_samples[0]["sample"].spoken_keywords).union(processed_samples[1]["sample"].spoken_keywords)),
        1,
    )
    raw_score = float(
        (embedding_similarity * 0.55)
        + (pitch_similarity * 0.1)
        + (formant_similarity * 0.15)
        + (rhythm_similarity * 0.1)
        + (lexical_overlap * 0.1)
    )
    calibrated_score, calibration_metadata = _calibrate_score(raw_score)
    conclusion = _phonetic_conclusion(calibrated_score)
    case.adverse_condition_flag = bool(adverse_warnings)
    case.adverse_condition_warnings = adverse_warnings
    case.detected_language = processed_samples[0]["sample"].detected_language or processed_samples[1]["sample"].detected_language
    case.final_decision_label = conclusion
    case.calibrated_score = calibrated_score
    case.evidential_strength = conclusion
    case.calibration_metadata = calibration_metadata
    case.model_versions = {
        "transcription": settings.FORENSICS_WHISPER_MODEL_SIZE,
        "speaker_embedding": settings.FORENSICS_SPEECHBRAIN_SOURCE,
        "language_identification": str(settings.FORENSICS_FASTTEXT_MODEL_PATH.name),
    }
    case.feature_versions = {
        "phonetic": "classical+opensmile-v1",
    }
    case.preprocessing_notes = sorted(set(adverse_warnings + ["secure-ingest", "sha256", "normalization"]))
    case.save()

    evidence_payload = {
        "case_number": case.case_number,
        "case_type": case.case_type,
        "conclusion_label": conclusion,
        "raw_score": raw_score,
        "calibrated_score": calibrated_score,
        "embedding_similarity": embedding_similarity,
        "pitch_similarity": pitch_similarity,
        "formant_similarity": formant_similarity,
        "rhythm_similarity": rhythm_similarity,
        "lexical_overlap": lexical_overlap,
        "sample_a": {
            "language": processed_samples[0]["sample"].detected_language,
            "transcript": processed_samples[0]["sample"].transcript_text,
            "keywords": processed_samples[0]["sample"].spoken_keywords,
            "quality": processed_samples[0]["quality"],
            "metrics": processed_samples[0]["metrics"],
            "sha256": processed_samples[0]["sample"].original_artifact.sha256,
        },
        "sample_b": {
            "language": processed_samples[1]["sample"].detected_language,
            "transcript": processed_samples[1]["sample"].transcript_text,
            "keywords": processed_samples[1]["sample"].spoken_keywords,
            "quality": processed_samples[1]["quality"],
            "metrics": processed_samples[1]["metrics"],
            "sha256": processed_samples[1]["sample"].original_artifact.sha256,
        },
        "warnings": adverse_warnings,
        "noise_removal_applied": case.noise_removal_applied,
        "calibration": calibration_metadata,
    }
    result, _ = AnalysisResult.objects.update_or_create(
        case=case,
        defaults={
            "raw_score": raw_score,
            "calibrated_score": calibrated_score,
            "conclusion_label": conclusion,
            "evidence_summary": "Phonetic comparison completed with speaker-embedding, prosodic, formant, and rhythm evidence.",
            "evidence_payload": evidence_payload,
            "methodology": "Local-only speaker comparison using faster-whisper, fastText, SpeechBrain ECAPA embeddings, Parselmouth, openSMILE, torchaudio, and heuristic logistic calibration.",
            "comparison_metrics": {
                "embedding_similarity": embedding_similarity,
                "pitch_similarity": pitch_similarity,
                "formant_similarity": formant_similarity,
                "rhythm_similarity": rhythm_similarity,
                "lexical_overlap": lexical_overlap,
            },
            "model_versions": case.model_versions,
            "feature_versions": case.feature_versions,
            "calibration_metadata": calibration_metadata,
            "validation_metadata": case.validation_metadata,
            "completed_at": timezone.now(),
        },
    )
    tracker.update(AnalysisJob.Stage.CALIBRATION, 82, "Calibrated the phonetic similarity score against the local scoring model.")
    tracker.update(AnalysisJob.Stage.REPORT_DRAFTING, 88, "Submitting the structured evidence package for mandatory report generation.")
    ensure_report_version(case=case, job=job)
    case.status = Case.Status.AWAITING_REVIEW
    case.progress_percentage = 100
    case.current_stage = AnalysisJob.Stage.COMPLETED
    case.save(update_fields=["status", "progress_percentage", "current_stage", "updated_at"])
    tracker.succeed(stage=AnalysisJob.Stage.COMPLETED, message="Phonetic analysis and mandatory report generation completed.")
    return result


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\r", " ").replace("\n", " ")).strip()


def _tokenize(text: str) -> list[str]:
    return LETTER_TOKEN_PATTERN.findall(text.casefold())


def _sentence_split(text: str) -> list[str]:
    candidates = re.split(r"(?<=[.!?])\s+", text.strip())
    return [candidate for candidate in candidates if candidate]


def _frequency_profile(tokens: list[str], candidates: set[str] | None = None) -> dict:
    counts = Counter(tokens if candidates is None else [token for token in tokens if token in candidates])
    total = sum(counts.values()) or 1
    return {token: count / total for token, count in counts.items()}


def _native_top_ngrams(text: str, analyzer: str, ngram_range: tuple[int, int], max_features: int) -> dict:
    min_n, max_n = ngram_range
    terms: list[str] = []

    if analyzer == "char":
        source = normalize_text(text).casefold()
        for size in range(min_n, max_n + 1):
            terms.extend(
                source[index : index + size]
                for index in range(max(len(source) - size + 1, 0))
                if source[index : index + size].strip()
            )
    elif analyzer == "word":
        tokens = _tokenize(text)
        for size in range(min_n, max_n + 1):
            terms.extend(" ".join(tokens[index : index + size]) for index in range(max(len(tokens) - size + 1, 0)))
    else:
        return {}

    return {term: count for term, count in Counter(terms).most_common(max_features)}


def _top_ngrams(text: str, analyzer: str, ngram_range: tuple[int, int], max_features: int = 20) -> dict:
    if CountVectorizer is not None:
        kwargs = {"analyzer": analyzer, "ngram_range": ngram_range, "max_features": max_features}
        if analyzer == "word":
            kwargs["token_pattern"] = r"(?u)\b\w+\b"
        try:
            vectorizer = CountVectorizer(**kwargs)
            matrix = vectorizer.fit_transform([text])
            values = matrix.toarray()[0]
            names = vectorizer.get_feature_names_out()
            return {name: int(value) for name, value in zip(names, values) if value > 0}
        except ValueError:
            pass
    return _native_top_ngrams(text, analyzer, ngram_range, max_features)


def _cosine_from_dicts(left: dict, right: dict) -> float:
    keys = sorted(set(left).union(right))
    if not keys:
        return 0.0
    vector_left = np.array([left.get(key, 0.0) for key in keys], dtype=float)
    vector_right = np.array([right.get(key, 0.0) for key in keys], dtype=float)
    denom = np.linalg.norm(vector_left) * np.linalg.norm(vector_right)
    return float(np.dot(vector_left, vector_right) / denom) if denom else 0.0


def _topic_similarity(left_text: str, right_text: str) -> float:
    if TfidfVectorizer is not None and cosine_similarity is not None:
        try:
            tfidf = TfidfVectorizer(stop_words="english", token_pattern=r"(?u)\b\w+\b")
            matrix = tfidf.fit_transform([left_text, right_text])
            return float(cosine_similarity(matrix[0], matrix[1])[0][0])
        except ValueError:
            pass

    left_profile = _frequency_profile([token for token in _tokenize(left_text) if token not in FUNCTION_WORDS])
    right_profile = _frequency_profile([token for token in _tokenize(right_text) if token not in FUNCTION_WORDS])
    return _cosine_from_dicts(left_profile, right_profile)


def _lexical_richness(tokens: list[str]) -> dict:
    unique = set(tokens)
    total = len(tokens) or 1
    return {
        "type_token_ratio": len(unique) / total,
        "hapax_ratio": len([token for token, count in Counter(tokens).items() if count == 1]) / total,
    }


def _sentence_length_stats(sentences: list[str]) -> dict:
    lengths = [len(_tokenize(sentence)) for sentence in sentences] or [0]
    return {
        "mean": float(np.mean(lengths)),
        "std": float(np.std(lengths)),
        "max": int(max(lengths)),
        "min": int(min(lengths)),
    }


def _token_length_stats(tokens: list[str]) -> dict:
    lengths = [len(token) for token in tokens] or [0]
    return {
        "mean": float(np.mean(lengths)),
        "std": float(np.std(lengths)),
        "max": int(max(lengths)),
        "min": int(min(lengths)),
    }


def _punctuation_profile(text: str) -> dict:
    punctuation = Counter(character for character in text if character in ".,;:!?-'\"()[]")
    total = sum(punctuation.values()) or 1
    return {key: value / total for key, value in punctuation.items()}


def _capitalization_profile(text: str) -> dict:
    uppercase = sum(1 for char in text if char.isupper())
    lowercase = sum(1 for char in text if char.islower())
    total = max(uppercase + lowercase, 1)
    title_case_words = len([word for word in re.findall(r"\b\w+\b", text) if word.istitle()])
    return {
        "uppercase_ratio": uppercase / total,
        "title_case_words": title_case_words,
    }


def _repeated_phrases(tokens: list[str]) -> list[str]:
    bigrams = [" ".join(tokens[index : index + 2]) for index in range(len(tokens) - 1)]
    counts = Counter(bigrams)
    return [phrase for phrase, count in counts.most_common(10) if count > 1]


def _spelling_habits(tokens: list[str]) -> dict:
    suffixes = Counter(token[-2:] for token in tokens if len(token) > 3)
    common = suffixes.most_common(10)
    return {suffix: count for suffix, count in common}


def _feature_contributions(profiles: dict) -> dict:
    return {name: round(value, 4) for name, value in profiles.items()}


def run_linguistic_pipeline(case: Case, job: AnalysisJob) -> AnalysisResult:
    tracker = JobTracker(job)
    tracker.start(AnalysisJob.Stage.PREPROCESSING, "Starting linguistic normalization and stylometric preparation.")
    case.status = Case.Status.RUNNING
    case.save(update_fields=["status", "updated_at"])

    samples_by_role = {sample.role: sample for sample in case.text_samples.all()}
    if set(samples_by_role) != {TextSample.SampleRole.SUSPECTED, TextSample.SampleRole.PROVIDED}:
        raise RuntimeError("Linguistic analysis requires exactly one suspected text sample and one provided text sample.")

    suspected = samples_by_role[TextSample.SampleRole.SUSPECTED]
    provided = samples_by_role[TextSample.SampleRole.PROVIDED]
    samples = [suspected, provided]
    adverse_warnings: list[str] = []
    extracted_features: dict[str, dict] = {}

    for index, sample in enumerate(samples, start=1):
        _check_cancel_requested(job)
        normalized_text = normalize_text(sample.raw_text)
        tokens = _tokenize(normalized_text)
        sentences = _sentence_split(normalized_text)
        language = detect_language(normalized_text)
        sample.normalized_text = normalized_text
        sample.detected_language = language
        sample.text_length = len(normalized_text)
        sample.token_count = len(tokens)
        sample.sentence_count = len(sentences)
        sample.encoding_warnings = []
        sample.preprocessing_steps = ["sha256", "whitespace-normalization", "tokenization"]

        if len(tokens) < 80:
            adverse_warnings.append(f"{sample.get_role_display()} has too little text for stable authorship comparison.")
        if not normalized_text.isascii():
            sample.encoding_warnings = ["Non-ASCII characters detected; verify normalization and source encoding."]
        sample.save()

        char_ngrams = _top_ngrams(normalized_text, analyzer="char", ngram_range=(3, 5))
        word_ngrams = _top_ngrams(normalized_text, analyzer="word", ngram_range=(1, 2))
        function_word_profile = _frequency_profile(tokens, FUNCTION_WORDS)
        punctuation_profile = _punctuation_profile(normalized_text)
        capitalization_profile = _capitalization_profile(sample.raw_text)
        sentence_stats = _sentence_length_stats(sentences)
        token_stats = _token_length_stats(tokens)
        lexical_richness = _lexical_richness(tokens)
        repeated_phrases = _repeated_phrases(tokens)
        spelling_habits = _spelling_habits(tokens)
        whitespace_patterns = {
            "double_space_count": sample.raw_text.count("  "),
            "tab_count": sample.raw_text.count("\t"),
            "line_break_count": sample.raw_text.count("\n"),
        }
        pos_patterns = {}
        contributions = _feature_contributions(
            {
                "type_token_ratio": lexical_richness["type_token_ratio"],
                "mean_sentence_length": sentence_stats["mean"],
                "uppercase_ratio": capitalization_profile["uppercase_ratio"],
            }
        )

        LinguisticFeatureSet.objects.update_or_create(
            case=case,
            sample=sample,
            defaults={
                "role": sample.role,
                "char_ngrams": char_ngrams,
                "word_ngrams": word_ngrams,
                "function_word_frequencies": function_word_profile,
                "punctuation_profile": punctuation_profile,
                "capitalization_profile": capitalization_profile,
                "sentence_length_stats": sentence_stats,
                "token_length_stats": token_stats,
                "lexical_richness": lexical_richness,
                "repeated_phrases": repeated_phrases,
                "spelling_habits": spelling_habits,
                "whitespace_patterns": whitespace_patterns,
                "pos_patterns": pos_patterns,
                "feature_contributions": contributions,
            },
        )
        extracted_features[sample.role] = {
            "language": language,
            "char_ngrams": char_ngrams,
            "word_ngrams": word_ngrams,
            "function_word_frequencies": function_word_profile,
            "punctuation_profile": punctuation_profile,
            "capitalization_profile": capitalization_profile,
            "sentence_length_stats": sentence_stats,
            "token_length_stats": token_stats,
            "lexical_richness": lexical_richness,
            "repeated_phrases": repeated_phrases,
            "spelling_habits": spelling_habits,
            "whitespace_patterns": whitespace_patterns,
            "pos_patterns": pos_patterns,
        }
        tracker.update(
            AnalysisJob.Stage.FEATURE_EXTRACTION,
            20 + (index * 20),
            f"Extracted stylometric feature set for {sample.get_role_display()}.",
            metadata={"last_sample": sample.role},
        )

    _check_cancel_requested(job)
    tracker.update(AnalysisJob.Stage.COMPARISON, 70, "Comparing stylometric profiles for both texts.")
    suspected_features = extracted_features[TextSample.SampleRole.SUSPECTED]
    provided_features = extracted_features[TextSample.SampleRole.PROVIDED]
    char_similarity = _cosine_from_dicts(suspected_features["char_ngrams"], provided_features["char_ngrams"])
    word_similarity = _cosine_from_dicts(suspected_features["word_ngrams"], provided_features["word_ngrams"])
    function_word_similarity = _cosine_from_dicts(
        suspected_features["function_word_frequencies"],
        provided_features["function_word_frequencies"],
    )
    punctuation_similarity = _cosine_from_dicts(
        suspected_features["punctuation_profile"],
        provided_features["punctuation_profile"],
    )
    sentence_similarity = _normalize_similarity(
        suspected_features["sentence_length_stats"]["mean"] - provided_features["sentence_length_stats"]["mean"],
        12.0,
    )
    lexical_similarity = _normalize_similarity(
        suspected_features["lexical_richness"]["type_token_ratio"] - provided_features["lexical_richness"]["type_token_ratio"],
        0.4,
    )
    topic_similarity = _topic_similarity(suspected.normalized_text, provided.normalized_text)
    topic_mismatch = 1.0 - topic_similarity
    raw_score = float(
        (char_similarity * 0.25)
        + (word_similarity * 0.2)
        + (function_word_similarity * 0.2)
        + (punctuation_similarity * 0.1)
        + (sentence_similarity * 0.1)
        + (lexical_similarity * 0.1)
        + (topic_similarity * 0.05)
    )
    calibrated_score, calibration_metadata = _calibrate_score(raw_score, {"offset": -0.1, "scale": 4.6})
    conclusion = _linguistic_conclusion(calibrated_score)
    shared_markers = sorted(
        set(suspected_features["repeated_phrases"]).intersection(provided_features["repeated_phrases"])
    )[:10]
    divergent_markers = sorted(
        set(suspected_features["repeated_phrases"]).symmetric_difference(provided_features["repeated_phrases"])
    )[:10]
    if suspected.detected_language and provided.detected_language and suspected.detected_language != provided.detected_language:
        adverse_warnings.append("Language mismatch detected between the compared texts.")
    if topic_mismatch > 0.6:
        adverse_warnings.append("Marked topic mismatch detected across the compared texts.")

    comparison_metrics = {
        "char_similarity": char_similarity,
        "word_similarity": word_similarity,
        "function_word_similarity": function_word_similarity,
        "punctuation_similarity": punctuation_similarity,
        "sentence_similarity": sentence_similarity,
        "lexical_similarity": lexical_similarity,
        "topic_similarity": topic_similarity,
    }
    stylometry_engine = "sklearn-stylometry-v1" if CountVectorizer is not None else "native-stylometry-v1"
    topic_engine = "sklearn-tfidf-v1" if TfidfVectorizer is not None and cosine_similarity is not None else "token-frequency-v1"

    case.adverse_condition_flag = bool(adverse_warnings)
    case.adverse_condition_warnings = adverse_warnings
    case.detected_language = suspected.detected_language or provided.detected_language
    case.final_decision_label = conclusion
    case.calibrated_score = calibrated_score
    case.evidential_strength = conclusion
    case.calibration_metadata = calibration_metadata
    case.model_versions = {
        "language_identification": str(settings.FORENSICS_FASTTEXT_MODEL_PATH.name),
        "stylometry": stylometry_engine,
        "topic_similarity": topic_engine,
    }
    case.feature_versions = {
        "linguistic": "ngrams-function-words-punctuation-v1",
    }
    case.preprocessing_notes = ["sha256", "tokenization", "stylometric-feature-extraction"]
    case.save()

    if hasattr(case, "linguistic_case"):
        case.linguistic_case.language = case.detected_language
        case.linguistic_case.text_length_warning = any(sample.token_count < 80 for sample in samples)
        case.linguistic_case.topic_mismatch_score = topic_mismatch
        case.linguistic_case.comparison_summary = {
            "raw_score": raw_score,
            "calibrated_score": calibrated_score,
            "conclusion_label": conclusion,
            "metrics": comparison_metrics,
            "warnings": adverse_warnings,
        }
        case.linguistic_case.save(
            update_fields=[
                "language",
                "text_length_warning",
                "topic_mismatch_score",
                "comparison_summary",
                "updated_at",
            ]
        )

    evidence_payload = {
        "case_number": case.case_number,
        "case_type": case.case_type,
        "raw_score": raw_score,
        "calibrated_score": calibrated_score,
        "conclusion_label": conclusion,
        "topic_similarity": topic_similarity,
        "topic_mismatch": topic_mismatch,
        "char_similarity": char_similarity,
        "word_similarity": word_similarity,
        "function_word_similarity": function_word_similarity,
        "punctuation_similarity": punctuation_similarity,
        "sentence_similarity": sentence_similarity,
        "lexical_similarity": lexical_similarity,
        "suspected_sample": {
            "sha256": suspected.sha256,
            "language": suspected.detected_language,
            "text_length": suspected.text_length,
            "token_count": suspected.token_count,
            "features": suspected_features,
        },
        "provided_sample": {
            "sha256": provided.sha256,
            "language": provided.detected_language,
            "text_length": provided.text_length,
            "token_count": provided.token_count,
            "features": provided_features,
        },
        "shared_markers": shared_markers,
        "divergent_markers": divergent_markers,
        "warnings": adverse_warnings,
        "calibration": calibration_metadata,
    }
    result, _ = AnalysisResult.objects.update_or_create(
        case=case,
        defaults={
            "raw_score": raw_score,
            "calibrated_score": calibrated_score,
            "conclusion_label": conclusion,
            "evidence_summary": "Linguistic comparison completed with n-gram, function-word, punctuation, lexical-richness, and sentence-structure evidence.",
            "evidence_payload": evidence_payload,
            "methodology": "Local-only stylometric comparison using fastText language identification where available, Unicode tokenization, n-gram feature profiles, optional scikit-learn vectorizers, and heuristic logistic calibration.",
            "top_shared_markers": shared_markers,
            "top_divergent_markers": divergent_markers,
            "comparison_metrics": comparison_metrics,
            "model_versions": case.model_versions,
            "feature_versions": case.feature_versions,
            "calibration_metadata": calibration_metadata,
            "validation_metadata": case.validation_metadata,
            "completed_at": timezone.now(),
        },
    )
    tracker.update(AnalysisJob.Stage.CALIBRATION, 82, "Calibrated the stylometric similarity score against the local scoring model.")
    tracker.update(AnalysisJob.Stage.REPORT_DRAFTING, 88, "Submitting the structured evidence package for mandatory report generation.")
    ensure_report_version(case=case, job=job)
    case.status = Case.Status.AWAITING_REVIEW
    case.progress_percentage = 100
    case.current_stage = AnalysisJob.Stage.COMPLETED
    case.save(update_fields=["status", "progress_percentage", "current_stage", "updated_at"])
    tracker.succeed(stage=AnalysisJob.Stage.COMPLETED, message="Linguistic analysis and mandatory report generation completed.")
    return result
