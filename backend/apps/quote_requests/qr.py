"""QR code for the open link, as an SVG string.

Uses reportlab's QR encoder (already installed for PDF export), so the
plug-in needs no extra dependency.
"""

from reportlab.graphics.barcode import qrencoder

QUIET_ZONE = 4  # modules of white border the QR spec requires for reliable scanning


def qr_svg(text: str, *, size_attrs: bool = False) -> str:
    code = qrencoder.QRCode(None, qrencoder.QRErrorCorrectLevel.M)
    code.addData(text)
    code.make()
    n = code.getModuleCount()
    total = n + 2 * QUIET_ZONE
    cells = "".join(
        f"M{c + QUIET_ZONE},{r + QUIET_ZONE}h1v1h-1z"
        for r in range(n)
        for c in range(n)
        if code.isDark(r, c)
    )
    dims = f' width="{total * 8}" height="{total * 8}"' if size_attrs else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} {total}"{dims} '
        f'shape-rendering="crispEdges" role="img" aria-label="QR code">'
        f'<rect width="{total}" height="{total}" fill="#fff"/><path d="{cells}" fill="#000"/></svg>'
    )
