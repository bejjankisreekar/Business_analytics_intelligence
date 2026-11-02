"""Printable barcode labels for products, categories and sub-categories.

Labels print as an A4 sticker sheet, 3 across and 8 down (63.5 x 33.9 mm per
label, the common sticker size). Each label has the item name on top and a
Code 128 barcode underneath, so a shop can stick them on shelves, bins and
products, and scan them on the Scan & Bill screen.
"""
from io import BytesIO

from reportlab.graphics.barcode import code128
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

COLUMNS = 3
ROWS = 8
LABEL_W = 63.5 * mm
LABEL_H = 33.9 * mm
MARGIN_X = (A4[0] - COLUMNS * LABEL_W) / 2
MARGIN_Y = (A4[1] - ROWS * LABEL_H) / 2
TITLE_FONT = "Helvetica-Bold"
TITLE_SIZE = 8
MAX_BAR_WIDTH = 0.35 * mm
MIN_BAR_WIDTH = 0.18 * mm


def _fit_title(text: str, max_width: float) -> str:
    """Shortens `text` with an ellipsis so it fits on one line of the label."""
    if stringWidth(text, TITLE_FONT, TITLE_SIZE) <= max_width:
        return text
    while text and stringWidth(text + "…", TITLE_FONT, TITLE_SIZE) > max_width:
        text = text[:-1]
    return text + "…"


def _barcode(code: str, bar_width: float) -> code128.Code128:
    return code128.Code128(code, barWidth=bar_width, barHeight=12 * mm, humanReadable=True, fontSize=7)


def _draw_label(c: canvas.Canvas, x: float, y: float, title: str, code: str) -> None:
    c.setFont(TITLE_FONT, TITLE_SIZE)
    c.drawCentredString(
        x + LABEL_W / 2,
        y + LABEL_H - 5 * mm,
        _fit_title(title, LABEL_W - 6 * mm),
    )
    # Shrink the bars until the code fits the label width.
    bar_width = MAX_BAR_WIDTH
    barcode = _barcode(code, bar_width)
    while barcode.width > LABEL_W - 8 * mm and bar_width > MIN_BAR_WIDTH:
        bar_width -= 0.02 * mm
        barcode = _barcode(code, bar_width)
    barcode.drawOn(c, x + (LABEL_W - barcode.width) / 2, y + 3 * mm)


def labels_pdf(labels: list[tuple[str, str]]) -> bytes:
    """One A4 PDF with a label for each (title, code) pair, in order."""
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    c.setTitle("Barcode labels")
    per_page = COLUMNS * ROWS
    for index, (title, code) in enumerate(labels):
        if index and index % per_page == 0:
            c.showPage()
        slot = index % per_page
        column = slot % COLUMNS
        row = slot // COLUMNS
        x = MARGIN_X + column * LABEL_W
        y = A4[1] - MARGIN_Y - (row + 1) * LABEL_H
        _draw_label(c, x, y, title, code)
    c.save()
    return buffer.getvalue()
