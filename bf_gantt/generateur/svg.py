# -*- coding: utf-8 -*-
"""Le SVG, écrit à la main depuis la même géométrie.

Pas de bibliothèque : un Gantt n'est fait que de rectangles, de lignes et de
texte, et le SVG est un format texte. Écrire les balises soi-même coûte moins
cher qu'une dépendance, et donne un fichier qu'un designer peut ouvrir dans
Inkscape ou Illustrator sans conversion.

⚠️ Tout texte qui vient de la base est échappé. Un nom de tâche contenant `&`
ou `<` casserait le document, et un nom fabriqué exprès y glisserait du script.
"""
import re
from xml.sax.saxutils import escape, quoteattr

from . import geometrie as geo

# Une couleur de marque vient de `res.company`, donc d'une écriture d'usager.
# Elle finit dans un attribut `fill=` d'un document servi à des visiteurs non
# connectés : on ne la recopie que si elle a la forme d'une couleur.
COULEUR = re.compile(r"^#[0-9A-Fa-f]{3,8}$")

# Largeur moyenne d'un caractère de Lexend, en fraction du corps. Sert à couper
# les libellés. Le SVG n'a pas de mesure de police, et le rendu final dépend de
# la police du lecteur : on approche, on ne prétend pas mesurer.
RATIO = 0.55


def _mesurer(corps):
    return lambda texte: len(texte or "") * corps * RATIO


def rendre(payload, echelle="week", zoom=1.0):
    """Rend le SVG en octets (UTF-8).

    `zoom` n'agit que sur la boîte du document : le `viewBox` reste le repère de
    la géométrie, donc aucune coordonnée ne change et le tracé reste net à
    n'importe quel facteur. C'est du vectoriel, pas un agrandissement d'image.
    """
    zoom = geo.borner_zoom(zoom, defaut=1.0)
    g = geo.construire(payload, echelle=echelle)
    accent = g["societe"].get("color") or "#29ABE1"
    if not COULEUR.match(str(accent)):
        accent = "#29ABE1"
    out = []
    a = out.append

    a('<?xml version="1.0" encoding="UTF-8"?>')
    a('<svg xmlns="http://www.w3.org/2000/svg" '
      'width="%.0f" height="%.0f" viewBox="0 0 %.0f %.0f" '
      'font-family="Lexend, Inter, system-ui, sans-serif">'
      % (g["largeur"] * zoom, g["hauteur"] * zoom, g["largeur"], g["hauteur"]))
    a('<rect width="100%%" height="100%%" fill="#ffffff"/>')

    # Bandes de fin de semaine.
    for bande in g["bandes"]:
        a('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"/>'
          % (bande["x"], g["y_axe"], bande["largeur"],
             g["y_fin_lignes"] - g["y_axe"], geo.FOND_COULOIR))

    # Bandeau de titre.
    a('<rect x="%.1f" y="%.1f" width="3.2" height="26" fill="%s"/>'
      % (geo.MARGE_PAGE, geo.MARGE_PAGE + 4, accent))
    a('<text x="%.1f" y="%.1f" font-size="15" font-weight="600" fill="%s">%s</text>'
      % (geo.MARGE_PAGE + 11, geo.MARGE_PAGE + 18, geo.ENCRE, escape(g["titre"])))
    sous = " · ".join(p for p in [g["sous_titre"], g["societe"].get("name", "")] if p)
    if sous:
        a('<text x="%.1f" y="%.1f" font-size="8.5" fill="%s">%s</text>'
          % (geo.MARGE_PAGE + 11, geo.MARGE_PAGE + 31, geo.GRIS, escape(sous)))
    # Le logo de la société, en haut à droite. Embarqué en data URI : le fichier
    # reste autonome, ce qui est le point d'un SVG qu'on envoie par courriel.
    droite = g["largeur"] - geo.MARGE_PAGE
    y_repere = geo.MARGE_PAGE + 18
    logo = g.get("logo")
    if logo:
        # Un logo vectoriel s'embarque aussi bien qu'un matriciel : c'est même
        # le seul rendu où il ressort net à n'importe quelle taille.
        # `b64` et `mime` viennent de la base : ils passent par `quoteattr`,
        # qui échappe les guillemets que `escape()` laisse passer.
        a('<image x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
          'preserveAspectRatio="xMaxYMin meet" href=%s/>'
          % (droite - logo["largeur"], geo.MARGE_PAGE + 2,
             logo["largeur"], logo["hauteur"],
             quoteattr("data:%s;base64,%s" % (logo["mime"], logo["b64"]))))
        y_repere = geo.MARGE_PAGE + 6 + logo["hauteur"] + 9
    a('<text x="%.1f" y="%.1f" font-size="8" text-anchor="end" fill="%s">'
      '%s lignes · %s au %s</text>'
      % (droite, y_repere, geo.GRIS, g["compte"], g["debut"], g["fin"]))

    # Graduations.
    for haut in g["graduations"]["haut"]:
        a('<text x="%.1f" y="%.1f" font-size="7.5" font-weight="600" fill="%s">%s</text>'
          % (haut["x"] + 3, g["y_axe"] + 11, geo.GRIS, escape(haut["texte"])))
    for bas in g["graduations"]["bas"]:
        a('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
          'stroke-width="0.4"/>'
          % (bas["x"], g["y_axe"] + 16, bas["x"], g["y_fin_lignes"], geo.FILET))
        if bas["largeur"] >= 12:
            a('<text x="%.1f" y="%.1f" font-size="6.8" fill="%s">%s</text>'
              % (bas["x"] + 2, g["y_axe"] + 28, geo.GRIS, escape(bas["texte"])))

    # Couloirs.
    couper = geo.couper
    for couloir in g["couloirs"]:
        a('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"/>'
          % (geo.MARGE_PAGE, couloir["y"], g["largeur"] - 2 * geo.MARGE_PAGE,
             couloir["hauteur"], geo.FOND_COULOIR))
        a('<text x="%.1f" y="%.1f" font-size="8.5" font-weight="600" fill="%s">%s</text>'
          % (geo.MARGE_PAGE + 6, couloir["y"] + 17, geo.ENCRE,
             escape(couper(couloir["name"], g["largeur_libelles"] - 62,
                           _mesurer(8.5)))))
        a('<text x="%.1f" y="%.1f" font-size="7.5" text-anchor="end" fill="%s">'
          '%s/%s · %s %%</text>'
          % (geo.MARGE_PAGE + g["largeur_libelles"] - 8, couloir["y"] + 17,
             geo.GRIS, couloir["done"], couloir["total"], couloir["pct"]))

    # Flèches, sous les barres.
    a('<g stroke="%s" stroke-width="0.6" fill="none">' % geo.GRIS)
    for fleche in g["fleches"]:
        chemin = " ".join("%s%.1f,%.1f" % ("M" if i == 0 else "L", x, y)
                          for i, (x, y) in enumerate(fleche["points"]))
        a('<path d="%s"/>' % chemin)
    a('</g>')
    for fleche in g["fleches"]:
        px, py = fleche["pointe"]
        a('<path d="M%.1f,%.1f L%.1f,%.1f L%.1f,%.1f Z" fill="%s"/>'
          % (px, py, px - 4.0, py - 2.2, px - 4.0, py + 2.2, geo.GRIS))

    # Lignes.
    for ligne in g["lignes"]:
        largeur_libelle = g["largeur_libelles"] - 20
        if ligne["assignee"]:
            largeur_libelle -= 58
        a('<text x="%.1f" y="%.1f" font-size="8" fill="%s">%s</text>'
          % (geo.MARGE_PAGE + 14, ligne["y"] + 15, geo.ENCRE,
             escape(couper(ligne["name"], largeur_libelle, _mesurer(8)))))
        if ligne["assignee"]:
            a('<text x="%.1f" y="%.1f" font-size="7" text-anchor="end" fill="%s">%s</text>'
              % (geo.MARGE_PAGE + g["largeur_libelles"] - 8, ligne["y"] + 15,
                 geo.GRIS, escape(couper(ligne["assignee"], 54, _mesurer(7)))))

        if ligne["is_milestone"]:
            d = ligne["diamant"]
            a('<path d="M%.1f,%.1f L%.1f,%.1f L%.1f,%.1f L%.1f,%.1f Z" fill="%s"/>'
              % (d["cx"], d["cy"] - d["r"], d["cx"] + d["r"], d["cy"],
                 d["cx"], d["cy"] + d["r"], d["cx"] - d["r"], d["cy"],
                 ligne["couleur_plein"]))
            continue

        a('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="2.5" fill="%s"/>'
          % (ligne["bar_x"], ligne["bar_y"], ligne["bar_w"], ligne["bar_h"],
             ligne["couleur_fond"]))
        if ligne["fill_w"] > 0.5:
            a('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="2.5" fill="%s"/>'
              % (ligne["bar_x"], ligne["bar_y"], ligne["fill_w"], ligne["bar_h"],
                 ligne["couleur_plein"]))
        for cote in ("avant", "apres"):
            if not ligne.get("deborde_" + cote):
                continue
            bord = (ligne["bar_x"] if cote == "avant"
                    else ligne["bar_x"] + ligne["bar_w"])
            sens = -1.0 if cote == "avant" else 1.0
            milieu = ligne["bar_y"] + ligne["bar_h"] / 2.0
            a('<path d="M%.1f,%.1f L%.1f,%.1f L%.1f,%.1f Z" fill="%s"/>'
              % (bord + sens * 5.0, milieu, bord, milieu - 4.0,
                 bord, milieu + 4.0, ligne["couleur_plein"]))
        if ligne["approx"]:
            a('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
              'stroke-width="0.8" stroke-dasharray="1.5 1.5"/>'
              % (ligne["bar_x"], ligne["bar_y"], ligne["bar_x"],
                 ligne["bar_y"] + ligne["bar_h"], geo.GRIS))
        a('<title>%s</title>' % escape("%s · %s au %s · %s %%" % (
            ligne["name"], ligne["start"], ligne["end"], ligne["progress"])))

    # Ligne d'aujourd'hui.
    if g.get("x_aujourdhui") is not None:
        x = g["x_aujourdhui"]
        a('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
          'stroke-width="0.9" stroke-dasharray="3 2"/>'
          % (x, g["y_lignes"], x, g["y_fin_lignes"], geo.LIGNE_AUJOURDHUI))
        # Au-dessus de l'axe : sous les graduations, il chevauchait la date.
        a('<text x="%.1f" y="%.1f" font-size="6.5" fill="%s">aujourd\'hui</text>'
          % (x + 2, g["y_axe"] - 3, geo.LIGNE_AUJOURDHUI))

    # Pied.
    y = g["hauteur"] - geo.MARGE_PAGE - 10
    a('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="0.5"/>'
      % (geo.MARGE_PAGE, y - 8, g["largeur"] - geo.MARGE_PAGE, y - 8, geo.FILET))
    pied = g["societe"].get("name", "")
    if g["societe"].get("tagline"):
        pied = "%s · %s" % (pied, g["societe"]["tagline"])
    a('<text x="%.1f" y="%.1f" font-size="7" fill="%s">%s</text>'
      % (geo.MARGE_PAGE, y + 4, geo.GRIS, escape(pied)))
    notes = []
    if g["tronque"]:
        notes.append("liste tronquée")
    if g.get("plage_reduite"):
        notes.append("plage ramenée à %d ans, chevron = barre qui dépasse"
                     % (geo.PLAGE_MAX_JOURS // 366))
    notes.append("trait pointillé = début approximatif")
    a('<text x="%.1f" y="%.1f" font-size="7" text-anchor="end" fill="%s">%s</text>'
      % (g["largeur"] - geo.MARGE_PAGE, y + 4, geo.GRIS, escape(" · ".join(notes))))

    a('</svg>')
    return "\n".join(out).encode("utf-8")
