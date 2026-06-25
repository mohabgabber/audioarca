from __future__ import annotations

from pathlib import Path
import re
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "USER_MANUAL.md"
OUTPUT = ROOT / "USER_MANUAL.pdf"


def build_styles():
    stylesheet = getSampleStyleSheet()
    styles = {
        "body": ParagraphStyle(
            "Body",
            parent=stylesheet["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=15,
            alignment=TA_JUSTIFY,
            textColor=colors.black,
            spaceAfter=8,
        ),
        "h1": ParagraphStyle(
            "Heading1Custom",
            parent=stylesheet["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=24,
            textColor=colors.black,
            spaceBefore=8,
            spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "Heading2Custom",
            parent=stylesheet["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=19,
            textColor=colors.black,
            spaceBefore=12,
            spaceAfter=8,
        ),
        "h3": ParagraphStyle(
            "Heading3Custom",
            parent=stylesheet["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=11.5,
            leading=15,
            textColor=colors.black,
            spaceBefore=10,
            spaceAfter=6,
        ),
        "h4": ParagraphStyle(
            "Heading4Custom",
            parent=stylesheet["Heading4"],
            fontName="Helvetica-Bold",
            fontSize=10.5,
            leading=14,
            textColor=colors.black,
            spaceBefore=8,
            spaceAfter=4,
        ),
    }
    return styles


def inline_markup(text: str) -> str:
    escaped = escape(text)
    escaped = re.sub(
        r"`([^`]+)`",
        lambda match: f'<font name="Courier">{escape(match.group(1))}</font>',
        escaped,
    )
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
    return escaped

def add_code_block(story, code_lines):
    if not code_lines:
        return
    code = "\n".join(code_lines).rstrip()
    block = Preformatted(
        code,
        ParagraphStyle(
            "CodeBlock",
            fontName="Courier",
            fontSize=8.6,
            leading=11,
            leftIndent=8,
            rightIndent=8,
            textColor=colors.black,
            spaceBefore=4,
            spaceAfter=4,
        ),
    )
    story.append(block)
    story.append(Spacer(1, 6))


def bullet_style(level: int):
    return ParagraphStyle(
        f"BulletLevel{level}",
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.black,
        leftIndent=16 + (level * 14),
        firstLineIndent=0,
        spaceAfter=4,
    )


def ordered_style(level: int):
    return ParagraphStyle(
        f"OrderedLevel{level}",
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.black,
        leftIndent=12 + (level * 14),
        firstLineIndent=0,
        spaceAfter=4,
    )


def render_markdown(story, lines, styles):
    paragraph_buffer: list[str] = []
    code_buffer: list[str] = []
    in_code = False

    def flush_paragraph():
        if paragraph_buffer:
            text = " ".join(part.strip() for part in paragraph_buffer if part.strip())
            story.append(Paragraph(inline_markup(text), styles["body"]))
            paragraph_buffer.clear()

    for raw in lines:
        line = raw.rstrip("\n")
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_paragraph()
            if in_code:
                add_code_block(story, code_buffer)
                code_buffer.clear()
                in_code = False
            else:
                in_code = True
            continue

        if in_code:
            code_buffer.append(line)
            continue

        if not stripped:
            flush_paragraph()
            story.append(Spacer(1, 2))
            continue

        heading_match = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if heading_match:
            flush_paragraph()
            level = len(heading_match.group(1))
            text = inline_markup(heading_match.group(2).strip())
            style_name = {1: "h1", 2: "h2", 3: "h3", 4: "h4"}[level]
            story.append(Paragraph(text, styles[style_name]))
            continue

        bullet_match = re.match(r"^(\s*)-\s+(.*)$", line)
        if bullet_match:
            flush_paragraph()
            level = len(bullet_match.group(1)) // 2
            story.append(Paragraph(f"&#8226; {inline_markup(bullet_match.group(2).strip())}", bullet_style(level)))
            continue

        ordered_match = re.match(r"^(\s*)(\d+)\.\s+(.*)$", line)
        if ordered_match:
            flush_paragraph()
            level = len(ordered_match.group(1)) // 2
            number = ordered_match.group(2)
            story.append(Paragraph(f"{number}. {inline_markup(ordered_match.group(3).strip())}", ordered_style(level)))
            continue

        paragraph_buffer.append(line)

    flush_paragraph()
    if code_buffer:
        add_code_block(story, code_buffer)


def draw_page(canvas, doc):
    canvas.saveState()
    width, height = A4
    canvas.setFont("Helvetica", 8.5)
    canvas.setFillColor(colors.black)
    canvas.drawRightString(width - 18 * mm, height - 11 * mm, str(doc.page))
    canvas.restoreState()


def main():
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    styles = build_styles()

    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=22 * mm,
        bottomMargin=18 * mm,
        title="User Manual",
        author="Forensic Analysis Toolkit",
    )

    story = []
    render_markdown(story, lines, styles)

    doc.build(story, onFirstPage=draw_page, onLaterPages=draw_page)


if __name__ == "__main__":
    main()
