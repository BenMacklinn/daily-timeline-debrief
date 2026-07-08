from __future__ import annotations

import re
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

from debrief.models import DailyDebrief, RowDebrief

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _markup(text: str, *, allow_bold: bool = True) -> str:
    escaped = escape(text)
    if not allow_bold:
        return escaped
    return _BOLD_RE.sub(r"<b>\1</b>", escaped)


def _bullet(text: str, style: ParagraphStyle, *, allow_bold: bool = True) -> Paragraph:
    return Paragraph(_markup(text, allow_bold=allow_bold), style, bulletText="-")


def _row_story(row: RowDebrief, debrief: DailyDebrief, styles: dict[str, ParagraphStyle]) -> list:
    story: list = [
        Paragraph("TBPN Rundown", styles["Title"]),
        Paragraph(
            f"{debrief.date_iso} | Row {escape(row.row)}"
            + (f" | {escape(row.tag)}" if row.tag else "")
            + f" | {escape(debrief.model)} ({escape(debrief.reasoning_effort)})",
            styles["Meta"],
        ),
        Spacer(1, 0.22 * inch),
        Paragraph(_markup(row.headline), styles["Headline"]),
    ]

    sections: list[tuple[str, list[str], bool]] = [
        ("Key news", row.key_news, True),
        ("Background", row.background, True),
        ("Hard facts", row.hard_facts, False),
    ]

    for title, bullets, allow_bold in sections:
        if not bullets:
            continue
        story.extend([Spacer(1, 0.22 * inch), Paragraph(title.upper(), styles["Section"])])
        for item in bullets:
            story.append(_bullet(item, styles["Bullet"], allow_bold=allow_bold))

    return story


def write_pdf(debrief: DailyDebrief, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)

    base = getSampleStyleSheet()
    styles = {
        "Title": ParagraphStyle(
            "DebriefTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=14,
            alignment=0,
            spaceAfter=4,
        ),
        "Meta": ParagraphStyle(
            "DebriefMeta",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#555555"),
        ),
        "Headline": ParagraphStyle(
            "DebriefHeadline",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=18,
            spaceAfter=4,
        ),
        "Section": ParagraphStyle(
            "DebriefSection",
            parent=base["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#555555"),
            spaceAfter=5,
        ),
        "Bullet": ParagraphStyle(
            "DebriefBullet",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            leftIndent=14,
            firstLineIndent=0,
            bulletIndent=2,
            spaceAfter=4,
        ),
    }

    story: list = []
    for index, row in enumerate(debrief.rows):
        if index:
            story.append(PageBreak())
        story.extend(_row_story(row, debrief, styles))

    doc = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        rightMargin=0.6 * inch,
        leftMargin=0.6 * inch,
        topMargin=0.45 * inch,
        bottomMargin=0.45 * inch,
        title=f"TBPN Rundown {debrief.date_iso}",
        author="Daily Timeline Debrief",
    )
    doc.build(story)
    return path
