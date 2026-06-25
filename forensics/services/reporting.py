from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import re

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.template.loader import render_to_string
from django.utils.html import escape
from django.utils import timezone

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - installed in runtime image
    OpenAI = None

try:
    from weasyprint import HTML
except ImportError:  # pragma: no cover - installed in runtime image
    HTML = None

from forensics.models import AnalysisJob, Case, EvidenceArtifact, Report, ReportVersion
from forensics.services.helpers import generate_report_number, log_event, persist_generated_file


SECTION_HEADINGS = (
    "Executive Summary",
    "Methodology",
    "Observations",
    "Interpretation",
    "Limitations",
    "Reviewer Notes",
)

NUMERIC_TOKEN_RE = re.compile(r"(?<!\d)\d+(?:\.\d+)?(?!\d)")
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
ORDERED_LIST_RE = re.compile(r"^\d+[.)]\s+(.+)$")


def _number_variants(token: str) -> set[str]:
    variants = {token}
    try:
        decimal_value = Decimal(token)
    except InvalidOperation:
        return variants

    normalized = format(decimal_value.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    normalized = normalized or "0"
    if normalized == "-0":
        normalized = "0"
    variants.add(normalized)
    variants.add(format(decimal_value.quantize(Decimal("0.01")), "f"))
    if decimal_value == decimal_value.to_integral():
        variants.add(str(decimal_value.quantize(Decimal("1"))))
    return {variant for variant in variants if variant}


def _flatten_numbers(value) -> set[str]:
    numbers: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            numbers.update(_flatten_numbers(key))
            numbers.update(_flatten_numbers(item))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            numbers.update(_flatten_numbers(item))
    elif isinstance(value, bool) or value is None:
        return numbers
    else:
        for token in NUMERIC_TOKEN_RE.findall(str(value)):
            numbers.update(_number_variants(token))
    return numbers


def _extract_report_numbers(text: str) -> list[str]:
    numbers: list[str] = []
    for raw_line in text.replace("\r\n", "\n").split("\n"):
        line = raw_line
        if re.match(r"^\s*#{1,6}\s*", line):
            line = re.sub(r"^\s*#{1,6}\s*", "", line)
        line = re.sub(r"^\s*\d+[.)]\s+", "", line)
        numbers.extend(NUMERIC_TOKEN_RE.findall(line))
    return numbers


def validate_report_numbers(text: str, evidence_snapshot: dict) -> None:
    allowed = _flatten_numbers(evidence_snapshot)
    discovered = _extract_report_numbers(text)
    unexpected = [token for token in discovered if token not in allowed]
    if unexpected:
        raise ValueError(f"Generated report referenced numeric values not found in the evidence snapshot: {unexpected[:10]}")


def build_report_prompt(case: Case, evidence_snapshot: dict) -> tuple[str, str]:
    system_prompt = (
        "You are drafting a formal forensic analysis report narrative. "
        "Use only the supplied structured evidence. Do not invent metrics, methods, facts, or conclusions. "
        "Do not introduce numeric values unless they already appear in the evidence payload. "
        "Keep the required section headings exactly as provided and do not prefix them with numbers. "
        "Return clear markdown with the headings: Executive Summary, Methodology, Observations, Interpretation, Limitations, Reviewer Notes."
    )
    user_prompt = (
        "Transform the structured evidence into a formal, readable report narrative for a court, investigator, analyst, or reviewer.\n\n"
        f"Case metadata:\n{json.dumps({'case_number': case.case_number, 'case_name': case.name, 'case_type': case.case_type}, indent=2)}\n\n"
        f"Structured evidence:\n{json.dumps(evidence_snapshot, indent=2, ensure_ascii=False)}"
    )
    return system_prompt, user_prompt


def parse_report_sections(markdown: str) -> list[dict]:
    sections: list[dict] = []
    current_heading = SECTION_HEADINGS[0]
    current_lines: list[str] = []
    heading_lookup = {heading.lower(): heading for heading in SECTION_HEADINGS}

    for raw_line in markdown.replace("\r\n", "\n").split("\n"):
        stripped = raw_line.strip()
        normalized = stripped.lstrip("#").strip().rstrip(":")
        normalized = re.sub(r"^\d+[.)]\s+", "", normalized).rstrip(":")
        if normalized.lower() in heading_lookup:
            if current_lines or not sections:
                sections.append({"heading": current_heading, "body": "\n".join(current_lines).strip()})
            current_heading = heading_lookup[normalized.lower()]
            current_lines = []
            continue
        current_lines.append(raw_line)

    if current_lines or not sections:
        sections.append({"heading": current_heading, "body": "\n".join(current_lines).strip()})

    ordered_sections: list[dict] = []
    existing = {section["heading"]: section["body"] for section in sections}
    for heading in SECTION_HEADINGS:
        ordered_sections.append({"heading": heading, "body": existing.get(heading, "")})
    return ordered_sections


def _render_emphasis(text: str) -> str:
    html = escape(text)
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"__(.+?)__", r"<strong>\1</strong>", html)
    return html


def _render_inline_markdown(text: str) -> str:
    rendered: list[str] = []
    position = 0
    for match in INLINE_CODE_RE.finditer(text):
        if match.start() > position:
            rendered.append(_render_emphasis(text[position : match.start()]))
        rendered.append(f"<code>{escape(match.group(1))}</code>")
        position = match.end()
    if position < len(text):
        rendered.append(_render_emphasis(text[position:]))
    return "".join(rendered)


def render_section_body_html(body: str) -> str:
    if not body:
        return "<p>No narrative supplied.</p>"

    parts: list[str] = []
    paragraph: list[str] = []
    unordered_items: list[str] = []
    ordered_items: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            parts.append(f"<p>{_render_inline_markdown(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_unordered_list() -> None:
        if unordered_items:
            items = "".join(f"<li>{_render_inline_markdown(item)}</li>" for item in unordered_items)
            parts.append(f"<ul>{items}</ul>")
            unordered_items.clear()

    def flush_ordered_list() -> None:
        if ordered_items:
            items = "".join(f"<li>{_render_inline_markdown(item)}</li>" for item in ordered_items)
            parts.append(f"<ol>{items}</ol>")
            ordered_items.clear()

    def flush_lists() -> None:
        flush_unordered_list()
        flush_ordered_list()

    for raw_line in body.splitlines() + [""]:
        stripped = raw_line.strip()
        if not stripped:
            flush_paragraph()
            flush_lists()
            continue

        heading_match = MARKDOWN_HEADING_RE.match(stripped)
        if heading_match:
            flush_paragraph()
            flush_lists()
            heading_level = len(heading_match.group(1))
            tag = "h3" if heading_level <= 2 else "h4"
            parts.append(f"<{tag}>{_render_inline_markdown(heading_match.group(2).strip())}</{tag}>")
            continue

        if stripped.startswith(("- ", "* ")):
            flush_paragraph()
            flush_ordered_list()
            unordered_items.append(stripped[2:].strip())
            continue

        ordered_match = ORDERED_LIST_RE.match(stripped)
        if ordered_match:
            flush_paragraph()
            flush_unordered_list()
            ordered_items.append(ordered_match.group(1).strip())
            continue

        if unordered_items or ordered_items:
            flush_lists()
        paragraph.append(stripped)

    flush_paragraph()
    flush_lists()
    return "".join(parts) or "<p>No narrative supplied.</p>"


def _report_case_status_label(case: Case) -> str:
    if case.status == Case.Status.RUNNING and hasattr(case, "analysis_result"):
        return Case.Status.AWAITING_REVIEW.label
    return case.get_status_display()


def render_report_markdown(case: Case, evidence_snapshot: dict) -> tuple[str, dict]:
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY must be configured for mandatory report generation.")
    if OpenAI is None:
        raise RuntimeError("The OpenAI Python client is required for mandatory report generation.")

    system_prompt, user_prompt = build_report_prompt(case, evidence_snapshot)
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.responses.create(
        model=settings.FORENSICS_REPORT_MODEL,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    markdown = response.output_text.strip()
    validate_report_numbers(markdown, evidence_snapshot)
    metadata = {
        "model": settings.FORENSICS_REPORT_MODEL,
        "response_id": response.id,
        "created_at": timezone.now().isoformat(),
    }
    return markdown, {"system_prompt": system_prompt, "user_prompt": user_prompt, "response": metadata}


def render_report_html(*, case: Case, report_version: ReportVersion, evidence_snapshot: dict, markdown: str) -> str:
    report_sections = [
        {
            "heading": section["heading"],
            "body": section["body"],
            "html": render_section_body_html(section["body"]),
        }
        for section in parse_report_sections(markdown)
    ]
    return render_to_string(
        "forensics/reports/pdf.html",
        {
            "case": case,
            "report_version": report_version,
            "report": report_version.report,
            "evidence": evidence_snapshot,
            "report_sections": report_sections,
            "generated_at": timezone.now(),
            "case_status_label": _report_case_status_label(case),
            "report_status_label": ReportVersion.Status.READY.label,
            "report_model_name": report_version.model_name or settings.FORENSICS_REPORT_MODEL,
        },
    )


def render_pdf(html: str, output_path: Path) -> None:
    if HTML is None:
        raise RuntimeError("WeasyPrint must be installed to render PDF reports.")
    HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf(str(output_path))


def ensure_report_version(*, case: Case, job: AnalysisJob) -> ReportVersion:
    if not hasattr(case, "analysis_result"):
        raise RuntimeError("A report cannot be generated until structured analysis evidence exists.")

    evidence_snapshot = case.analysis_result.evidence_payload
    with transaction.atomic():
        report, _ = Report.objects.select_for_update().get_or_create(
            case=case,
            defaults={
                "report_number": generate_report_number(),
                "status": Report.Status.PENDING,
                "generated_by": case.created_by,
            },
        )
        version = (
            ReportVersion.objects.select_for_update()
            .filter(report=report, generated_by_job=job)
            .order_by("-version", "-created_at")
            .first()
        )
        if version and version.status == ReportVersion.Status.READY:
            return version
        if report.status != Report.Status.PENDING:
            report.status = Report.Status.PENDING
            report.save(update_fields=["status", "updated_at"])

        if version is None:
            latest_existing_version = ReportVersion.objects.filter(report=report).aggregate(max_version=Max("version"))["max_version"] or 0
            next_version = max(report.latest_version_number, latest_existing_version) + 1
            version = ReportVersion.objects.create(
                report=report,
                version=next_version,
                status=ReportVersion.Status.PENDING,
                generated_by_job=job,
                evidence_snapshot=evidence_snapshot,
            )
        else:
            next_version = version.version
            version.status = ReportVersion.Status.PENDING
            version.evidence_snapshot = evidence_snapshot
            version.prompt_text = ""
            version.prompt_metadata = {}
            version.rendered_html = ""
            version.rendered_markdown = ""
            version.generated_at = None
            version.failure_reason = ""
            version.save(
                update_fields=[
                    "status",
                    "evidence_snapshot",
                    "prompt_text",
                    "prompt_metadata",
                    "rendered_html",
                    "rendered_markdown",
                    "generated_at",
                    "failure_reason",
                    "updated_at",
                ]
            )

    try:
        markdown, prompt_metadata = render_report_markdown(case, evidence_snapshot)
        html = render_report_html(case=case, report_version=version, evidence_snapshot=evidence_snapshot, markdown=markdown)
    except Exception as exc:
        version.status = ReportVersion.Status.FAILED
        version.failure_reason = str(exc)
        version.save(update_fields=["status", "failure_reason", "updated_at"])
        report.status = Report.Status.FAILED
        report.save(update_fields=["status", "updated_at"])
        raise

    with TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        html_path = temp_root / f"{report.report_number}-v{next_version}.html"
        pdf_path = temp_root / version.pdf_filename
        html_path.write_text(html, encoding="utf-8")
        render_pdf(html, pdf_path)

        html_artifact = persist_generated_file(
            case=case,
            source_path=html_path,
            artifact_type=EvidenceArtifact.ArtifactType.REPORT_HTML,
            role="report",
            filename=html_path.name,
            mime_type="text/html",
            created_by=case.created_by,
        )
        pdf_artifact = persist_generated_file(
            case=case,
            source_path=pdf_path,
            artifact_type=EvidenceArtifact.ArtifactType.REPORT_PDF,
            role="report",
            filename=version.pdf_filename,
            mime_type="application/pdf",
            created_by=case.created_by,
            derived_from=html_artifact,
        )

    version.status = ReportVersion.Status.READY
    version.model_name = settings.FORENSICS_REPORT_MODEL
    version.prompt_text = prompt_metadata["user_prompt"]
    version.prompt_metadata = prompt_metadata
    version.rendered_markdown = markdown
    version.rendered_html = html
    version.pdf_artifact = pdf_artifact
    version.generated_at = timezone.now()
    version.save(
        update_fields=[
            "status",
            "model_name",
            "prompt_text",
            "prompt_metadata",
            "rendered_markdown",
            "rendered_html",
            "pdf_artifact",
            "generated_at",
            "updated_at",
        ]
    )
    report.status = Report.Status.READY
    report.latest_version_number = next_version
    report.last_generated_at = version.generated_at
    report.save(update_fields=["status", "latest_version_number", "last_generated_at", "updated_at"])
    log_event(
        event_type="report",
        title="Report version generated",
        message=f"Generated report version {next_version} for case {case.case_number}.",
        case=case,
        actor=case.created_by,
        details={
            "report_number": report.report_number,
            "version": next_version,
            "model": settings.FORENSICS_REPORT_MODEL,
        },
    )
    return version
