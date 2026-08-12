"""Estampage d'un filigrane d'identification sur un PDF, en mémoire.

Module-level helper (même forme que ``s3.py`` / ``sms.py``) : reportlab pour
l'overlay, le lecteur/écrivain exposé par ``odoo.tools.pdf`` pour la fusion.
Les deux libs sont dans l'image de tous les locataires ; ``reportlab`` est
déclarée au manifeste pour que l'install échoue net sur une image qui ne
l'aurait pas.
"""
import io

from reportlab.lib import colors
from reportlab.pdfgen import canvas

from odoo.tools import pdf as _otools_pdf


def stamp_pdf_bytes(src_bytes, lines):
    """Retourne ``src_bytes`` (PDF) avec, sur chaque page, un filigrane
    diagonal répété + une ligne de pied lisible.

    Lève en cas d'échec (PDF chiffré, corrompu, …) — l'appelant décide du repli.
    """
    text = "   ·   ".join([ln for ln in lines if ln])
    reader = _otools_pdf.PdfReader(io.BytesIO(src_bytes))
    writer = _otools_pdf.PdfWriter()
    for page in reader.pages:
        box = page.mediabox
        width = float(box.width)
        height = float(box.height)

        buf = io.BytesIO()
        overlay = canvas.Canvas(buf, pagesize=(width, height))

        # Filigrane diagonal répété, très pâle (ne gêne pas la lecture).
        overlay.saveState()
        overlay.translate(width / 2.0, height / 2.0)
        overlay.rotate(45)
        overlay.setFont("Helvetica-Bold", 13)
        overlay.setFillColor(colors.Color(0.80, 0.10, 0.10, alpha=0.12))
        step = 92
        span = int(max(width, height) / step) + 2
        for i in range(-span, span + 1):
            overlay.drawCentredString(0, i * step, text)
        overlay.restoreState()

        # Ligne de pied lisible.
        overlay.setFont("Helvetica", 7)
        overlay.setFillColor(colors.Color(0, 0, 0, alpha=0.55))
        overlay.drawString(18, 12, text)

        overlay.showPage()
        overlay.save()
        buf.seek(0)

        page.merge_page(_otools_pdf.PdfReader(buf).pages[0])
        writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
