# -*- coding: utf-8 -*-
"""Le PNG, tracé avec Pillow depuis la même géométrie.

Pourquoi une image en plus du PDF : parce qu'on colle un échéancier dans un
courriel, dans une infolettre ou dans une planche de présentation, et qu'aucun
de ces trois endroits n'affiche un PDF.

Pillow est déjà dans l'image Odoo. Le tracé se fait à 2x puis se réduit, ce qui
donne des bords propres sans dépendre d'un antialiasing que Pillow n'a pas sur
les rectangles.
"""
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import geometrie as geo

POLICES = Path(__file__).resolve().parent.parent / "static" / "fonts"
SUR_ECHANTILLON = 2
# Au-delà, on n'illustre plus, on remplit la mémoire. 60 Mpx font déjà une image
# de 7746 x 7746, soit largement plus qu'aucun écran ni aucune planche.
PIXELS_MAX = 60_000_000


def _police(taille, gras=False):
    fichier = "Lexend-SemiBold.ttf" if gras else "Lexend-Regular.ttf"
    try:
        return ImageFont.truetype(str(POLICES / fichier), taille)
    except Exception:
        return ImageFont.load_default()


def _rgb(hexa):
    hexa = (hexa or "#000000").lstrip("#")
    if len(hexa) == 3:
        hexa = "".join(c * 2 for c in hexa)
    return tuple(int(hexa[i:i + 2], 16) for i in (0, 2, 4))


def rendre(payload, echelle="week", echelle_image=SUR_ECHANTILLON, zoom=1.0):
    """Rend le PNG en octets.

    `zoom` agrandit l'image finale : le tracé est fait à `echelle_image × zoom`
    puis réduit à la taille demandée, donc un PNG à 200 % est **redessiné** plus
    grand, il n'est pas un agrandissement flou du petit.
    """
    zoom = geo.borner_zoom(zoom, defaut=1.0)
    g = geo.construire(payload, echelle=echelle)
    k = max(1, int(echelle_image))

    largeur = int(g["largeur"] * k * zoom)
    hauteur = int(g["hauteur"] * k * zoom)

    # 🔴 Un garde-fou qui remet les facteurs à 1 puis alloue quand même la taille
    # de la géométrie ne garde RIEN : une géométrie de 20 000 x 14 000 fait
    # 280 millions de pixels à facteur 1. On réduit donc jusqu'à passer sous le
    # plafond, quitte à descendre sous l'échelle 1:1.
    if largeur * hauteur > PIXELS_MAX:
        reduction = (PIXELS_MAX / float(largeur * hauteur)) ** 0.5
        k, zoom = 1, max(0.05, reduction)
        largeur = max(1, int(g["largeur"] * zoom))
        hauteur = max(1, int(g["hauteur"] * zoom))

    image = Image.new("RGB", (largeur, hauteur), (255, 255, 255))
    d = ImageDraw.Draw(image)

    polices = {
        "titre": _police(max(1, int(15 * k * zoom)), gras=True),
        "sous": _police(max(1, int(8.5 * k * zoom))),
        "couloir": _police(max(1, int(8.5 * k * zoom)), gras=True),
        "ligne": _police(max(1, int(8 * k * zoom))),
        "petit": _police(max(1, int(7 * k * zoom))),
        "grad": _police(max(1, int(6.8 * k * zoom))),
        "grad_haut": _police(max(1, int(7.5 * k * zoom)), gras=True),
        "marque": _police(max(1, int(13 * k * zoom)), gras=True),
    }

    facteur = k * zoom

    def X(v):
        return v * facteur

    def mesurer(police):
        def _m(texte):
            if not texte:
                return 0
            return d.textlength(texte, font=police) / facteur
        return _m

    accent = _rgb(g["societe"].get("color") or "#29ABE1")

    # Bandes de fin de semaine.
    for bande in g["bandes"]:
        d.rectangle([X(bande["x"]), X(g["y_axe"]),
                     X(bande["x"] + bande["largeur"]), X(g["y_fin_lignes"])],
                    fill=_rgb(geo.FOND_COULOIR))

    # Titre.
    d.rectangle([X(geo.MARGE_PAGE), X(geo.MARGE_PAGE + 4),
                 X(geo.MARGE_PAGE + 3.2), X(geo.MARGE_PAGE + 30)], fill=accent)
    d.text((X(geo.MARGE_PAGE + 11), X(geo.MARGE_PAGE + 4)), g["titre"],
           font=polices["titre"], fill=_rgb(geo.ENCRE))
    sous = " · ".join(p for p in [g["sous_titre"], g["societe"].get("name", "")] if p)
    if sous:
        d.text((X(geo.MARGE_PAGE + 11), X(geo.MARGE_PAGE + 24)), sous,
               font=polices["sous"], fill=_rgb(geo.GRIS))
    droite = g["largeur"] - geo.MARGE_PAGE
    y_repere = geo.MARGE_PAGE + 8
    logo = g.get("logo")
    if logo and not logo.get("vectoriel"):
        try:
            vignette = Image.open(io.BytesIO(logo["octets"]))
            cible = (max(1, int(X(logo["largeur"]))), max(1, int(X(logo["hauteur"]))))
            vignette = vignette.convert("RGBA").resize(cible, Image.LANCZOS)
            image.paste(vignette,
                        (int(X(droite - logo["largeur"])), int(X(geo.MARGE_PAGE + 2))),
                        vignette)
            y_repere = geo.MARGE_PAGE + 6 + logo["hauteur"] + 2
        except Exception:
            pass
    elif g["societe"].get("name"):
        # Voir pdf.py : pas de rasteriseur SVG dans l'image, donc un mot-symbole.
        d.text((X(droite), X(geo.MARGE_PAGE + 6)), g["societe"]["name"],
               font=polices["marque"], fill=accent, anchor="ra")
        y_repere = geo.MARGE_PAGE + 26
    repere = "%s lignes · %s au %s" % (g["compte"], g["debut"], g["fin"])
    d.text((X(droite), X(y_repere)), repere,
           font=polices["sous"], fill=_rgb(geo.GRIS), anchor="ra")

    # Graduations.
    for haut in g["graduations"]["haut"]:
        d.text((X(haut["x"] + 3), X(g["y_axe"] + 3)), haut["texte"],
               font=polices["grad_haut"], fill=_rgb(geo.GRIS))
    for bas in g["graduations"]["bas"]:
        d.line([X(bas["x"]), X(g["y_axe"] + 16),
                X(bas["x"]), X(g["y_fin_lignes"])], fill=_rgb(geo.FILET), width=1)
        if bas["largeur"] >= 12:
            d.text((X(bas["x"] + 2), X(g["y_axe"] + 21)), bas["texte"],
                   font=polices["grad"], fill=_rgb(geo.GRIS))

    # Couloirs.
    for couloir in g["couloirs"]:
        d.rectangle([X(geo.MARGE_PAGE), X(couloir["y"]),
                     X(g["largeur"] - geo.MARGE_PAGE),
                     X(couloir["y"] + couloir["hauteur"])],
                    fill=_rgb(geo.FOND_COULOIR))
        d.text((X(geo.MARGE_PAGE + 6), X(couloir["y"] + 7)),
               geo.couper(couloir["name"], g["largeur_libelles"] - 62,
                          mesurer(polices["couloir"])),
               font=polices["couloir"], fill=_rgb(geo.ENCRE))
        d.text((X(geo.MARGE_PAGE + g["largeur_libelles"] - 8), X(couloir["y"] + 8)),
               "%s/%s · %s %%" % (couloir["done"], couloir["total"], couloir["pct"]),
               font=polices["petit"], fill=_rgb(geo.GRIS), anchor="ra")

    # Flèches.
    for fleche in g["fleches"]:
        points = [(X(x), X(y)) for x, y in fleche["points"]]
        d.line(points, fill=_rgb(geo.GRIS), width=max(1, int(facteur // 2)),
               joint="curve")
        px, py = fleche["pointe"]
        d.polygon([(X(px), X(py)), (X(px - 4.0), X(py - 2.2)),
                   (X(px - 4.0), X(py + 2.2))], fill=_rgb(geo.GRIS))

    # Lignes.
    for ligne in g["lignes"]:
        largeur_libelle = g["largeur_libelles"] - 20
        if ligne["assignee"]:
            largeur_libelle -= 58
        d.text((X(geo.MARGE_PAGE + 14), X(ligne["y"] + 6)),
               geo.couper(ligne["name"], largeur_libelle, mesurer(polices["ligne"])),
               font=polices["ligne"], fill=_rgb(geo.ENCRE))
        if ligne["assignee"]:
            d.text((X(geo.MARGE_PAGE + g["largeur_libelles"] - 8), X(ligne["y"] + 7)),
                   geo.couper(ligne["assignee"], 54, mesurer(polices["petit"])),
                   font=polices["petit"], fill=_rgb(geo.GRIS), anchor="ra")

        if ligne["is_milestone"]:
            m = ligne["diamant"]
            d.polygon([(X(m["cx"]), X(m["cy"] - m["r"])),
                       (X(m["cx"] + m["r"]), X(m["cy"])),
                       (X(m["cx"]), X(m["cy"] + m["r"])),
                       (X(m["cx"] - m["r"]), X(m["cy"]))],
                      fill=_rgb(ligne["couleur_plein"]))
            continue

        d.rounded_rectangle(
            [X(ligne["bar_x"]), X(ligne["bar_y"]),
             X(ligne["bar_x"] + ligne["bar_w"]), X(ligne["bar_y"] + ligne["bar_h"])],
            radius=X(2.5), fill=_rgb(ligne["couleur_fond"]))
        if ligne["fill_w"] > 0.5:
            d.rounded_rectangle(
                [X(ligne["bar_x"]), X(ligne["bar_y"]),
                 X(ligne["bar_x"] + ligne["fill_w"]),
                 X(ligne["bar_y"] + ligne["bar_h"])],
                radius=X(2.5), fill=_rgb(ligne["couleur_plein"]))
        for cote in ("avant", "apres"):
            if not ligne.get("deborde_" + cote):
                continue
            bord = (ligne["bar_x"] if cote == "avant"
                    else ligne["bar_x"] + ligne["bar_w"])
            sens = -1.0 if cote == "avant" else 1.0
            milieu = ligne["bar_y"] + ligne["bar_h"] / 2.0
            d.polygon([(X(bord + sens * 5.0), X(milieu)),
                       (X(bord), X(milieu - 4.0)),
                       (X(bord), X(milieu + 4.0))],
                      fill=_rgb(ligne["couleur_plein"]))
        if ligne["approx"]:
            _pointille(d, X(ligne["bar_x"]), X(ligne["bar_y"]),
                       X(ligne["bar_y"] + ligne["bar_h"]), _rgb(geo.GRIS),
                       facteur)

    # Aujourd'hui.
    if g.get("x_aujourdhui") is not None:
        x = X(g["x_aujourdhui"])
        _pointille(d, x, X(g["y_lignes"]), X(g["y_fin_lignes"]),
                   _rgb(geo.LIGNE_AUJOURDHUI), facteur, pas=int(5 * facteur))
        # Au-dessus de l'axe : sous les graduations, il chevauchait la date.
        d.text((x + X(2), X(g["y_axe"] - 10)), "aujourd'hui",
               font=polices["grad"], fill=_rgb(geo.LIGNE_AUJOURDHUI))

    # Pied.
    y = g["hauteur"] - geo.MARGE_PAGE - 10
    d.line([X(geo.MARGE_PAGE), X(y - 8), X(g["largeur"] - geo.MARGE_PAGE), X(y - 8)],
           fill=_rgb(geo.FILET), width=1)
    pied = g["societe"].get("name", "")
    if g["societe"].get("tagline"):
        pied = "%s · %s" % (pied, g["societe"]["tagline"])
    d.text((X(geo.MARGE_PAGE), X(y - 3)), pied,
           font=polices["petit"], fill=_rgb(geo.GRIS))
    notes = (["liste tronquée"] if g["tronque"] else [])
    if g.get("plage_reduite"):
        notes.append("plage ramenée à %d ans, chevron = barre qui dépasse"
                     % (geo.PLAGE_MAX_JOURS // 366))
    notes.append("trait pointillé = début approximatif")
    d.text((X(g["largeur"] - geo.MARGE_PAGE), X(y - 3)), " · ".join(notes),
           font=polices["petit"], fill=_rgb(geo.GRIS), anchor="ra")

    if k > 1:
        image = image.resize((max(1, int(g["largeur"] * zoom)),
                              max(1, int(g["hauteur"] * zoom))), Image.LANCZOS)

    tampon = io.BytesIO()
    image.save(tampon, format="PNG", optimize=True)
    return tampon.getvalue()


def _pointille(d, x, y1, y2, couleur, facteur, pas=None):
    pas = max(1, pas or int(3 * facteur))
    y = y1
    while y < y2:
        d.line([x, y, x, min(y + pas, y2)], fill=couleur,
               width=max(1, int(facteur // 2)))
        y += pas * 2
