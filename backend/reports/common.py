"""Shared pieces of the PDF reports: the footer on every page, safe text and table styling."""

from __future__ import annotations

import io
import re
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

FOOTER = (
    "CONFIDENTIAL — Synthetic data for demonstration. Investigation risk is not a finding of fraud. "
    "Final decisions rest with the assigned investigator."
)
BANNED_PHRASES = ("fraud probability", "fraudster", "guilty")
_BANNED = re.compile("|".join(re.escape(p) for p in BANNED_PHRASES), re.IGNORECASE)
MARGIN = 16 * mm
FOOTER_SIZE = 7

styles = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=12)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=7.5, leading=9.5)
H1 = ParagraphStyle("h1", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=16, leading=19, alignment=0)
H2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=11.5, spaceBefore=10, spaceAfter=4)
H3 = ParagraphStyle("h3", parent=BODY, fontName="Helvetica-Bold", spaceBefore=4)
BULLET = ParagraphStyle("bullet", parent=BODY, leftIndent=12, bulletIndent=2)


def clean(text: object) -> str:
    """Plain text that the standard PDF fonts can draw, without the phrases a report must never use."""
    value = _BANNED.sub("[removed]", str(text if text is not None else ""))
    return value.encode("cp1252", "replace").decode("cp1252")


def para(text: object, style: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph(escape(clean(text)), style)


def cell(text: object, style: ParagraphStyle = SMALL) -> Paragraph:
    return Paragraph(escape(clean(text)), style)


def table(rows: list[list], widths: list[float] | None = None, header: bool = True) -> Table:
    out = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    commands = [
        ("FONTSIZE", (0, 0), (-1, -1), 7.5), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#b9bfc9")),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]  # fmt: skip
    if header:
        commands += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8ebf0")),
                     ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]  # fmt: skip
    out.setStyle(TableStyle(commands))
    return out


def head_row(labels: list[str]) -> list:
    return [cell(label, ParagraphStyle("th", parent=SMALL, fontName="Helvetica-Bold")) for label in labels]


def _decorate(title: str):
    def on_page(canvas, doc):
        canvas.saveState()
        width, _ = A4
        canvas.setFont("Helvetica", FOOTER_SIZE)
        canvas.setFillColor(colors.HexColor("#444444"))
        lines = simpleSplit(FOOTER, "Helvetica", FOOTER_SIZE, width - 2 * MARGIN)
        for i, line in enumerate(lines):
            canvas.drawString(MARGIN, 14 * mm - i * (FOOTER_SIZE + 2), line)
        canvas.drawRightString(width - MARGIN, 14 * mm + 4 + FOOTER_SIZE, f"{clean(title)} · page {doc.page}")
        canvas.restoreState()

    return on_page


def render(story: list, title: str) -> bytes:
    """Lay the story out on A4 pages, with the confidentiality footer on every one."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=14 * mm,
        bottomMargin=24 * mm, title=clean(title), author="ClaimShield Nexus",
    )  # fmt: skip
    on_page = _decorate(title)
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buffer.getvalue()


PAGE_WIDTH = A4[0] - 2 * MARGIN
