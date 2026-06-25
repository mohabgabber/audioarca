import hashlib
import mimetypes
import uuid
from pathlib import Path
from tempfile import NamedTemporaryFile

try:
    import magic
except ImportError:  # pragma: no cover - optional at runtime
    magic = None

from django.conf import settings
from django.core.files import File
from django.db import transaction

from forensics.models import Case, EvidenceArtifact, EventLog, Report


def generate_case_number(case_type: str) -> str:
    prefix = "PHN" if case_type == Case.CaseType.PHONETIC else "LNG"
    while True:
        candidate = f"{prefix}-{uuid.uuid4().hex[:10].upper()}"
        if not Case.objects.filter(case_number=candidate).exists():
            return candidate


def generate_report_number() -> str:
    while True:
        candidate = f"RPT-{uuid.uuid4().hex[:10].upper()}"
        if not Report.objects.filter(report_number=candidate).exists():
            return candidate


def sniff_mime_type(uploaded_file) -> str:
    head = uploaded_file.read(4096)
    uploaded_file.seek(0)
    if magic is not None:
        try:
            return magic.from_buffer(head, mime=True)
        except Exception:
            pass
    return uploaded_file.content_type or mimetypes.guess_type(uploaded_file.name)[0] or "application/octet-stream"


def hash_text_payload(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def persist_uploaded_artifact(
    *,
    case: Case,
    uploaded_file,
    artifact_type: str,
    role: str,
    created_by,
    is_original: bool = False,
    derived_from: EvidenceArtifact | None = None,
    processing_steps: list[str] | None = None,
    metadata: dict | None = None,
) -> EvidenceArtifact:
    hasher = hashlib.sha256()
    temp_file = NamedTemporaryFile(delete=False)
    size = 0

    try:
        uploaded_file.seek(0)
        mime_type = sniff_mime_type(uploaded_file)
        uploaded_file.seek(0)
        for chunk in uploaded_file.chunks():
            hasher.update(chunk)
            temp_file.write(chunk)
            size += len(chunk)
        temp_file.flush()
        uploaded_file.seek(0)

        with open(temp_file.name, "rb") as fh:
            artifact = EvidenceArtifact(
                case=case,
                artifact_type=artifact_type,
                role=role,
                original_filename=Path(uploaded_file.name).name,
                mime_type=mime_type,
                sha256=hasher.hexdigest(),
                file_size_bytes=size,
                immutable=True,
                is_original=is_original,
                processing_steps=processing_steps or [],
                metadata=metadata or {},
                derived_from=derived_from,
                created_by=created_by,
            )
            artifact.file.save(Path(uploaded_file.name).name, File(fh), save=False)
            artifact.save()
            return artifact
    finally:
        temp_file.close()
        Path(temp_file.name).unlink(missing_ok=True)


def persist_generated_file(
    *,
    case: Case,
    source_path: Path,
    artifact_type: str,
    role: str,
    filename: str,
    mime_type: str,
    created_by=None,
    derived_from: EvidenceArtifact | None = None,
    processing_steps: list[str] | None = None,
    metadata: dict | None = None,
) -> EvidenceArtifact:
    hasher = hashlib.sha256()
    with source_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            hasher.update(chunk)

    artifact = EvidenceArtifact(
        case=case,
        artifact_type=artifact_type,
        role=role,
        original_filename=filename,
        mime_type=mime_type,
        sha256=hasher.hexdigest(),
        file_size_bytes=source_path.stat().st_size,
        immutable=True,
        is_original=False,
        processing_steps=processing_steps or [],
        metadata=metadata or {},
        derived_from=derived_from,
        created_by=created_by,
    )
    with source_path.open("rb") as fh:
        artifact.file.save(filename, File(fh), save=False)
    artifact.save()
    return artifact


@transaction.atomic
def log_event(
    *,
    event_type: str,
    title: str,
    message: str,
    case: Case | None = None,
    actor=None,
    details: dict | None = None,
) -> EventLog:
    return EventLog.objects.create(
        case=case,
        actor=actor,
        event_type=event_type,
        title=title,
        message=message,
        details=details or {},
    )


def build_absolute_url(request, path: str) -> str:
    if request is None:
        base = f"https://{settings.APP_DOMAIN}" if not settings.DEBUG else f"http://{settings.APP_DOMAIN}"
        return f"{base}{path}"
    return request.build_absolute_uri(path)
