# -*- coding: utf-8 -*-
"""Le PDF, tracé côté serveur, depuis la géométrie.

Deux dépendances et pas une de plus :

* `reportlab`, déjà exigé par Odoo, donc rien à ajouter à l'image ;
* Lexend, embarquée dans `static/fonts/` sous licence OFL 1.1, parce qu'un
  tracé qui change de police change de largeurs.

La page est taillée sur l'échéancier, à l'échelle 1:1. Elle ne se réduit que si
elle dépasse la taille maximale d'une page PDF, et le facteur est alors écrit
au pied plutôt que subi en silence.

⚠️ Le repère de `geometrie` descend, celui du PDF monte. La conversion se fait
en un seul endroit, `_y`, et nulle part ailleurs.
"""
import io
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas

from . import geometrie as geo

POLICES = Path(__file__).resolve().parent.parent / "static" / "fonts"
REGULIER = "BfGanttLexend"
GRAS = "BfGanttLexend-SemiBold"

# Une page PDF ne dépasse pas 14 400 points (200 pouces) par côté.
COTE_MAX = 14000.0

_polices_posees = False


def _poser_polices():
    global _polices_posees
    if _polices_posees:
        return
    try:
        pdfmetrics.registerFont(TTFont(REGULIER, str(POLICES / "Lexend-Regular.ttf")))
        pdfmetrics.registerFont(TTFont(GRAS, str(POLICES / "Lexend-SemiBold.ttf")))
        _polices_posees = True
    except Exception:
        # Sans les fichiers, Helvetica fait le travail et le PDF sort quand même.
        globals()["REGULIER"] = "Helvetica"
        globals()["GRAS"] = "Helvetica-Bold"
        _polices_posees = True


def _rgb(hexa):
    hexa = (hexa or "#000000").lstrip("#")
    if len(hexa) == 3:
        hexa = "".join(c * 2 for c in hexa)
    return tuple(int(hexa[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def rendre(payload, echelle="week", titre_pied=""):
    """Rend le PDF en octets."""
    _poser_polices()
    g = geo.construire(payload, echelle=echelle)

    largeur, hauteur = g["largeur"], g["hauteur"]
    facteur = 1.0
    if largeur > COTE_MAX or hauteur > COTE_MAX:
        facteur = min(COTE_MAX / largeur, COTE_MAX / hauteur)

    tampon = io.BytesIO()
    c = rl_canvas.Canvas(tampon, pagesize=(largeur * facteur, hauteur * facteur))
    c.setTitle(g["titre"] or "Échéancier")
    c.setAuthor(g["societe"].get("name", ""))
    if facteur != 1.0:
        c.scale(facteur, facteur)

    def _y(valeur):
        """Du repère du dessin vers celui du PDF."""
        return hauteur - valeur

    def mesurer(texte, police=REGULIER, corps=8.0):
        return pdfmetrics.stringWidth(texte or "", police, corps)

    accent = _rgb(g["societe"].get("color") or "#29ABE1")

    _fond(c, largeur, hauteur)
    _entete(c, g, _y, mesurer, accent)
    _axe(c, g, _y)
    _couloirs(c, g, _y, mesurer)
    _fleches(c, g, _y)
    _barres(c, g, _y, mesurer)
    _aujourdhui(c, g, _y)
    _pied(c, g, _y, largeur, facteur, titre_pied)

    c.showPage()
    c.save()
    return tampon.getvalue()


# ------------------------------------------------------------------ morceaux

def _fond(c, largeur, hauteur):
    c.setFillColorRGB(1, 1, 1)
    c.rect(0, 0, largeur, hauteur, stroke=0, fill=1)


def _entete(c, g, _y, mesurer, accent):
    x = geo.MARGE_PAGE
    haut = geo.MARGE_PAGE

    c.setFillColorRGB(*accent)
    c.rect(x, _y(haut + 30), 3.2, 26, stroke=0, fill=1)

    c.setFillColorRGB(*_rgb(geo.ENCRE))
    c.setFont(GRAS, 15)
    c.drawString(x + 11, _y(haut + 14), g["titre"])

    sous = " · ".join(p for p in [g["sous_titre"], g["societe"].get("name", "")] if p)
    if sous:
        c.setFillColorRGB(*_rgb(geo.GRIS))
        c.setFont(REGULIER, 8.5)
        c.drawString(x + 11, _y(haut + 27), sous)

    # Le logo de la société, en haut à droite. Sans logo, le repère de lecture
    # remonte à sa place : la mise en page ne laisse pas de trou.
    droite = g["largeur"] - geo.MARGE_PAGE
    y_repere = haut + 14
    logo = g.get("logo")
    if logo and not logo.get("vectoriel"):
        largeur_logo, hauteur_logo = logo["largeur"], logo["hauteur"]
        try:
            from reportlab.lib.utils import ImageReader
            c.drawImage(ImageReader(io.BytesIO(logo["octets"])),
                        droite - largeur_logo, _y(haut + 2 + hauteur_logo),
                        width=largeur_logo, height=hauteur_logo,
                        mask="auto", preserveAspectRatio=True, anchor="ne")
            y_repere = haut + 6 + hauteur_logo + 10
        except Exception:
            # Un logo illisible ne fait pas tomber le document.
            pass
    elif g["societe"].get("name"):
        # 🔴 Logo vectoriel : reportlab ne dessine pas de SVG et l'image Odoo n'a
        # pas de rasteriseur. On écrit le nom de la société à la couleur de la
        # marque, ce qui reste une signature plutôt qu'un trou.
        c.setFillColorRGB(*accent)
        c.setFont(GRAS, 13)
        c.drawRightString(droite, _y(haut + 16), g["societe"]["name"])
        y_repere = haut + 30

    c.setFillColorRGB(*_rgb(geo.GRIS))
    c.setFont(REGULIER, 8)
    repere = "%s lignes · %s au %s" % (g["compte"], g["debut"], g["fin"])
    c.drawRightString(droite, _y(y_repere), repere)


def _axe(c, g, _y):
    y = g["y_axe"]
    bas = g["y_fin_lignes"]

    # Bandes de fin de semaine, sous tout le reste.
    c.setFillColorRGB(*_rgb(geo.FOND_COULOIR))
    for bande in g["bandes"]:
        c.rect(bande["x"], _y(bas), bande["largeur"], bas - y, stroke=0, fill=1)

    c.setFillColorRGB(*_rgb(geo.GRIS))
    c.setFont(GRAS, 7.5)
    for haut in g["graduations"]["haut"]:
        c.drawString(haut["x"] + 3, _y(y + 11), haut["texte"])

    c.setStrokeColorRGB(*_rgb(geo.FILET))
    c.setLineWidth(0.4)
    c.setFont(REGULIER, 6.8)
    for bas_grad in g["graduations"]["bas"]:
        c.line(bas_grad["x"], _y(y + 16), bas_grad["x"], _y(bas))
        if bas_grad["largeur"] >= 12:
            c.setFillColorRGB(*_rgb(geo.GRIS))
            c.drawString(bas_grad["x"] + 2, _y(y + 28), bas_grad["texte"])

    c.setStrokeColorRGB(*_rgb(geo.FILET))
    c.setLineWidth(0.6)
    c.line(geo.MARGE_PAGE, _y(y + geo.HAUTEUR_AXE),
           g["largeur"] - geo.MARGE_PAGE, _y(y + geo.HAUTEUR_AXE))


def _couloirs(c, g, _y, mesurer):
    for couloir in g["couloirs"]:
        y = couloir["y"]
        c.setFillColorRGB(*_rgb(geo.FOND_COULOIR))
        c.rect(geo.MARGE_PAGE, _y(y + couloir["hauteur"]),
               g["largeur"] - 2 * geo.MARGE_PAGE, couloir["hauteur"],
               stroke=0, fill=1)

        c.setFillColorRGB(*_rgb(geo.ENCRE))
        c.setFont(GRAS, 8.5)
        libelle = geo.couper(
            couloir["name"], g["largeur_libelles"] - 62,
            lambda t: mesurer(t, GRAS, 8.5))
        c.drawString(geo.MARGE_PAGE + 6, _y(y + 17), libelle)

        c.setFillColorRGB(*_rgb(geo.GRIS))
        c.setFont(REGULIER, 7.5)
        c.drawRightString(geo.MARGE_PAGE + g["largeur_libelles"] - 8, _y(y + 17),
                          "%s/%s · %s %%" % (couloir["done"], couloir["total"],
                                             couloir["pct"]))


def _barres(c, g, _y, mesurer):
    for ligne in g["lignes"]:
        # Libellé, à gauche.
        c.setFillColorRGB(*_rgb(geo.ENCRE))
        c.setFont(REGULIER, 8)
        largeur_libelle = g["largeur_libelles"] - 20
        if ligne["assignee"]:
            largeur_libelle -= 58
        texte = geo.couper(ligne["name"], largeur_libelle,
                           lambda t: mesurer(t, REGULIER, 8))
        c.drawString(geo.MARGE_PAGE + 14, _y(ligne["y"] + 15), texte)

        if ligne["assignee"]:
            c.setFillColorRGB(*_rgb(geo.GRIS))
            c.setFont(REGULIER, 7)
            c.drawRightString(geo.MARGE_PAGE + g["largeur_libelles"] - 8,
                              _y(ligne["y"] + 15),
                              geo.couper(ligne["assignee"], 54,
                                         lambda t: mesurer(t, REGULIER, 7)))

        if ligne["is_milestone"]:
            _diamant(c, ligne, _y)
            continue

        # La barre : le fond, puis le rempli d'avancement.
        c.setFillColorRGB(*_rgb(ligne["couleur_fond"]))
        c.roundRect(ligne["bar_x"], _y(ligne["bar_y"] + ligne["bar_h"]),
                    ligne["bar_w"], ligne["bar_h"], 2.5, stroke=0, fill=1)
        if ligne["fill_w"] > 0.5:
            c.setFillColorRGB(*_rgb(ligne["couleur_plein"]))
            c.roundRect(ligne["bar_x"], _y(ligne["bar_y"] + ligne["bar_h"]),
                        ligne["fill_w"], ligne["bar_h"], 2.5, stroke=0, fill=1)

        # Une barre coupée par la fenêtre le dit : un chevron au bord touché.
        for cote in ("avant", "apres"):
            if not ligne.get("deborde_" + cote):
                continue
            bord = (ligne["bar_x"] if cote == "avant"
                    else ligne["bar_x"] + ligne["bar_w"])
            sens = -1.0 if cote == "avant" else 1.0
            milieu = ligne["bar_y"] + ligne["bar_h"] / 2.0
            c.setFillColorRGB(*_rgb(ligne["couleur_plein"]))
            t = c.beginPath()
            t.moveTo(bord + sens * 5.0, _y(milieu))
            t.lineTo(bord, _y(milieu - 4.0))
            t.lineTo(bord, _y(milieu + 4.0))
            t.close()
            c.drawPath(t, stroke=0, fill=1)

        # Un début approximatif se dit : trait pointillé sur le bord gauche.
        if ligne["approx"]:
            c.setStrokeColorRGB(*_rgb(geo.GRIS))
            c.setLineWidth(0.8)
            c.setDash(1.5, 1.5)
            c.line(ligne["bar_x"], _y(ligne["bar_y"]),
                   ligne["bar_x"], _y(ligne["bar_y"] + ligne["bar_h"]))
            c.setDash()


def _diamant(c, ligne, _y):
    d = ligne["diamant"]
    c.setFillColorRGB(*_rgb(ligne["couleur_plein"]))
    p = c.beginPath()
    p.moveTo(d["cx"], _y(d["cy"] - d["r"]))
    p.lineTo(d["cx"] + d["r"], _y(d["cy"]))
    p.lineTo(d["cx"], _y(d["cy"] + d["r"]))
    p.lineTo(d["cx"] - d["r"], _y(d["cy"]))
    p.close()
    c.drawPath(p, stroke=0, fill=1)


def _fleches(c, g, _y):
    c.setStrokeColorRGB(*_rgb(geo.GRIS))
    c.setLineWidth(0.6)
    for fleche in g["fleches"]:
        points = fleche["points"]
        p = c.beginPath()
        p.moveTo(points[0][0], _y(points[0][1]))
        for x, y in points[1:]:
            p.lineTo(x, _y(y))
        c.drawPath(p, stroke=1, fill=0)
        # La pointe.
        px, py = fleche["pointe"]
        t = c.beginPath()
        t.moveTo(px, _y(py))
        t.lineTo(px - 4.0, _y(py - 2.2))
        t.lineTo(px - 4.0, _y(py + 2.2))
        t.close()
        c.setFillColorRGB(*_rgb(geo.GRIS))
        c.drawPath(t, stroke=0, fill=1)


def _aujourdhui(c, g, _y):
    x = g.get("x_aujourdhui")
    if x is None:
        return
    c.setStrokeColorRGB(*_rgb(geo.LIGNE_AUJOURDHUI))
    c.setLineWidth(0.9)
    c.setDash(3, 2)
    c.line(x, _y(g["y_axe"] + geo.HAUTEUR_AXE), x, _y(g["y_fin_lignes"]))
    c.setDash()
    c.setFillColorRGB(*_rgb(geo.LIGNE_AUJOURDHUI))
    c.setFont(REGULIER, 6.5)
    # Au-dessus de l'axe : sous les graduations, il chevauchait la date.
    c.drawString(x + 2, _y(g["y_axe"] - 3), "aujourd'hui")


def _pied(c, g, _y, largeur, facteur, titre_pied):
    y = g["hauteur"] - geo.MARGE_PAGE - 10
    c.setStrokeColorRGB(*_rgb(geo.FILET))
    c.setLineWidth(0.5)
    c.line(geo.MARGE_PAGE, _y(y - 8), largeur - geo.MARGE_PAGE, _y(y - 8))

    c.setFillColorRGB(*_rgb(geo.GRIS))
    c.setFont(REGULIER, 7)
    gauche = titre_pied or g["societe"].get("name", "")
    if g["societe"].get("tagline"):
        gauche = "%s · %s" % (gauche, g["societe"]["tagline"])
    c.drawString(geo.MARGE_PAGE, _y(y + 4), gauche)

    notes = []
    if facteur != 1.0:
        notes.append("réduit à %d %%" % round(facteur * 100))
    if g["tronque"]:
        notes.append("liste tronquée")
    if g.get("plage_reduite"):
        notes.append("plage ramenée à %d ans, chevron = barre qui dépasse"
                     % (geo.PLAGE_MAX_JOURS // 366))
    notes.append("trait pointillé = début approximatif")
    c.drawRightString(largeur - geo.MARGE_PAGE, _y(y + 4), " · ".join(notes))
