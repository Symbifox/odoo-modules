# -*- coding: utf-8 -*-
"""Second rendu : le PDF vectoriel, tracé depuis le MÊME plan.

La page est taillée sur le dessin, à l'échelle 1:1. Elle ne se réduit que si
elle dépasse la taille maximale d'une page PDF (14 400 pt), et le facteur est
alors écrit dans le pied plutôt que subi en silence.

⚠️ reportlab compte les ordonnées depuis le BAS, le plan depuis le HAUT. La
conversion se fait ici, en un seul endroit : `_y()`.
"""
import io

from reportlab.pdfgen import canvas as rl_canvas

from . import disposition as dsp
from . import mesure

PDF_MAX = 14400.0

ENCRE = (0.176, 0.188, 0.192)
GRIS = (0.42, 0.45, 0.47)
FILET = (0.835, 0.851, 0.863)
BLEU = (0.161, 0.671, 0.882)
BLANC = (1, 1, 1)

TEINTES = {
    "neutre": ((1, 1, 1), (0.788, 0.812, 0.831)),
    "bleu": ((0.918, 0.969, 0.992), (0.161, 0.671, 0.882)),
    "ambre": ((0.992, 0.965, 0.906), (0.839, 0.600, 0.122)),
    "vert": ((0.914, 0.969, 0.933), (0.106, 0.541, 0.294)),
    "rouge": ((0.992, 0.933, 0.925), (0.753, 0.227, 0.169)),
}


def rendre(plan, titre_document=None):
    """Rend le plan en octets PDF."""
    mesure.enregistrer_polices()
    echelle = 1.0
    if max(plan.largeur, plan.hauteur) > PDF_MAX:
        echelle = PDF_MAX / max(plan.largeur, plan.hauteur)
    largeur, hauteur = plan.largeur * echelle, plan.hauteur * echelle

    tampon = io.BytesIO()
    c = rl_canvas.Canvas(tampon, pagesize=(largeur, hauteur))
    c.setTitle(titre_document or plan.titre or "Organigramme")
    c.setAuthor("Les services de consultation Blue Fox, Inc.")
    if echelle != 1.0:
        c.scale(echelle, echelle)

    def y(valeur):
        return plan.hauteur - valeur

    c.setFillColorRGB(*BLANC)
    c.rect(0, 0, plan.largeur, plan.hauteur, stroke=0, fill=1)

    # En-tête
    if plan.titre:
        c.setFillColorRGB(*BLEU)
        c.rect(dsp.MARGE_PAGE, y(dsp.MARGE_PAGE + 18), 4, 20, stroke=0, fill=1)
        c.setFillColorRGB(*ENCRE)
        c.setFont(mesure.GRAS, 14.5)
        c.drawString(dsp.MARGE_PAGE + 12, y(dsp.MARGE_PAGE + 13), plan.titre)
    if plan.sous_titre:
        c.setFillColorRGB(*GRIS)
        c.setFont(mesure.REGULIER, 9.5)
        c.drawString(dsp.MARGE_PAGE + 12, y(dsp.MARGE_PAGE + 29), plan.sous_titre)

    # Arêtes. Les étiquettes attendent la fin, comme dans le SVG : une
    # étiquette cachée sous une boîte est un pourcentage perdu.
    etiquettes = []
    c.setLineWidth(1.1)
    for a in plan.aretes:
        c.setStrokeColorRGB(*FILET)
        if a.pointille:
            c.setDash(4, 3)
        else:
            c.setDash()
        chemin = c.beginPath()
        x0, y0 = a.points[0]
        chemin.moveTo(x0, y(y0))
        for x1, y1 in a.points[1:]:
            chemin.lineTo(x1, y(y1))
        c.drawPath(chemin, stroke=1, fill=0)
        c.setDash()
        fx, fy = a.points[-1]
        c.setFillColorRGB(*FILET)
        pointe = c.beginPath()
        pointe.moveTo(fx, y(fy))
        pointe.lineTo(fx - 3.2, y(fy - 4.6))
        pointe.lineTo(fx + 3.2, y(fy - 4.6))
        pointe.close()
        c.drawPath(pointe, stroke=0, fill=1)
        if a.etiquette:
            etiquettes.append((a.etiquette, a.etiquette_xy))

    # Boîtes
    for b in plan.boites:
        fond, contour = TEINTES.get(b.teinte, TEINTES["neutre"])
        if b.accent:
            contour = BLEU
        c.setFillColorRGB(*fond)
        c.setStrokeColorRGB(*contour)
        c.setLineWidth(2.6 if b.accent else 1.1)
        c.roundRect(b.x, y(b.y + b.h), b.w, b.h, 6, stroke=1, fill=1)
        ligne_y = b.y + dsp.MARGE_INT + dsp.T_TITRE
        c.setFillColorRGB(*ENCRE)
        c.setFont(mesure.GRAS, dsp.T_TITRE)
        for ligne in b.lignes_titre:
            c.drawCentredString(b.cx, y(ligne_y), ligne)
            ligne_y += dsp.T_TITRE * dsp.INTERLIGNE
        c.setFillColorRGB(*GRIS)
        c.setFont(mesure.REGULIER, dsp.T_SOUS)
        for ligne in b.lignes_sous:
            c.drawCentredString(b.cx, y(ligne_y + 1), ligne)
            ligne_y += dsp.T_SOUS * dsp.INTERLIGNE
        c.setFont(mesure.REGULIER, dsp.T_NOTE)
        for ligne in b.lignes_note:
            c.drawCentredString(b.cx, y(ligne_y + 1), ligne)
            ligne_y += dsp.T_NOTE * dsp.INTERLIGNE
        if b.lien:
            c.linkURL(b.lien, (b.x, y(b.y + b.h), b.x + b.w, y(b.y)), relative=0)

    for texte, (ex, ey) in etiquettes:
        larg = mesure.largeur(texte, 7.5, gras=True) + 9
        c.setFillColorRGB(*BLANC)
        c.setStrokeColorRGB(*FILET)
        c.setLineWidth(0.7)
        c.roundRect(ex - larg / 2, y(ey + 6.5), larg, 13, 3, stroke=1, fill=1)
        c.setFillColorRGB(*ENCRE)
        c.setFont(mesure.GRAS, 7.5)
        c.drawCentredString(ex, y(ey + 2.8), texte)

    # Légende et pied
    base = plan.hauteur - dsp.H_PIED + 8
    x = dsp.MARGE_PAGE
    for teinte, libelle in plan.legende:
        fond, contour = TEINTES.get(teinte, TEINTES["neutre"])
        c.setFillColorRGB(*fond)
        c.setStrokeColorRGB(*contour)
        c.setLineWidth(0.9)
        c.roundRect(x, y(base), 10, 10, 2, stroke=1, fill=1)
        c.setFillColorRGB(*GRIS)
        c.setFont(mesure.REGULIER, 8)
        c.drawString(x + 14, y(base), libelle)
        x += 22 + mesure.largeur(libelle, 8)
    pied = plan.pied or ""
    if echelle != 1.0:
        reduit = "Réduit à %.0f %% pour tenir dans une page PDF." % (echelle * 100)
        pied = (pied + " " + reduit).strip()
    if pied:
        c.setFillColorRGB(*GRIS)
        c.setFont(mesure.REGULIER, 7.5)
        c.drawRightString(plan.largeur - dsp.MARGE_PAGE, y(base), pied)

    c.showPage()
    c.save()
    return tampon.getvalue()
