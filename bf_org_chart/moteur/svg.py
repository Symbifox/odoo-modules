# -*- coding: utf-8 -*-
"""Premier rendu : le SVG de l'écran, tracé depuis le plan.

Aucune géométrie n'est calculée ici. Le fichier reprend `plan.boites` et
`plan.aretes` tels quels, dans le même repère que le PDF, avec la même police
embarquée : ce qui s'affiche est ce qui s'imprimera.

Le fond de page est peint explicitement en blanc. Sous le mode sombre de la
maison, un SVG transparent emprunterait le fond de l'hôte et l'encre
deviendrait illisible.
"""
from xml.sax.saxutils import escape, quoteattr

from . import disposition as dsp
from . import mesure
from . import modele

# Palette de la maison. Le bleu BF ne sert JAMAIS de couleur de texte : il
# habille les contours et les bandeaux, l'encre reste l'anthracite.
ENCRE = "#2D3031"
GRIS = "#6B7280"
FILET = "#D5D9DC"
BLEU = "#29ABE1"
PAPIER = "#FFFFFF"

TEINTES = {
    "neutre": ("#FFFFFF", "#C9CFD4"),
    "bleu": ("#EAF7FD", "#29ABE1"),
    "ambre": ("#FDF6E7", "#D6991F"),
    "vert": ("#E9F7EE", "#1B8A4B"),
    "rouge": ("#FDEEEC", "#C03A2B"),
}


def _texte(x, y, contenu, taille, couleur=ENCRE, gras=False, ancre="middle"):
    poids = ' font-weight="600"' if gras else ""
    return (
        '<text x="%.2f" y="%.2f" font-family="BfOrgChartLexend, Lexend, '
        'system-ui, sans-serif" font-size="%.2f" fill="%s"%s '
        'text-anchor="%s">%s</text>'
        % (x, y, taille, couleur, poids, ancre, escape(contenu))
    )


def rendre(plan, police_url="/bf_org_chart/static/fonts/"):
    """Rend le plan en une chaîne SVG autonome."""
    p = []
    p.append(
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" '
        'viewBox="0 0 %.2f %.2f" width="%.2f" height="%.2f" '
        'role="img" aria-label=%s>'
        % (plan.largeur, plan.hauteur, plan.largeur, plan.hauteur,
           quoteattr(plan.titre or "Organigramme"))
    )
    p.append(
        "<style>@font-face{font-family:'BfOrgChartLexend';font-style:normal;"
        "font-weight:400;src:url('%sLexend-Regular.ttf') format('truetype');}"
        "@font-face{font-family:'BfOrgChartLexend';font-style:normal;"
        "font-weight:600;src:url('%sLexend-SemiBold.ttf') format('truetype');}"
        "a{cursor:pointer;}</style>" % (police_url, police_url)
    )
    p.append('<rect x="0" y="0" width="%.2f" height="%.2f" fill="%s"/>'
             % (plan.largeur, plan.hauteur, PAPIER))

    # En-tête
    if plan.titre:
        p.append('<rect x="%.2f" y="%.2f" width="4" height="20" fill="%s"/>'
                 % (dsp.MARGE_PAGE, dsp.MARGE_PAGE - 2, BLEU))
        p.append(_texte(dsp.MARGE_PAGE + 12, dsp.MARGE_PAGE + 13, plan.titre,
                        14.5, ENCRE, True, "start"))
    if plan.sous_titre:
        p.append(_texte(dsp.MARGE_PAGE + 12, dsp.MARGE_PAGE + 29,
                        plan.sous_titre, 9.5, GRIS, False, "start"))

    # Arêtes d'abord : elles passent SOUS les boîtes. Les étiquettes, elles,
    # attendent la fin : une étiquette cachée sous une boîte est une donnée
    # perdue, et sur une détention c'est le pourcentage qui disparaît.
    etiquettes = []
    for a in plan.aretes:
        points = " ".join("%.2f,%.2f" % (x, y) for x, y in a.points)
        tirets = ' stroke-dasharray="4 3"' if a.pointille else ""
        p.append('<polyline points="%s" fill="none" stroke="%s" '
                 'stroke-width="1.1"%s/>' % (points, FILET, tirets))
        fin = a.points[-1]
        p.append('<path d="M %.2f %.2f l -3.2 -4.6 l 6.4 0 z" fill="%s"/>'
                 % (fin[0], fin[1], FILET))
        if a.etiquette:
            ex, ey = a.etiquette_xy
            larg = mesure.largeur(a.etiquette, 7.5, gras=True) + 9
            etiquettes.append(
                '<rect x="%.2f" y="%.2f" width="%.2f" height="13" rx="3" '
                'fill="%s" stroke="%s" stroke-width="0.7"/>'
                % (ex - larg / 2, ey - 6.5, larg, PAPIER, FILET))
            etiquettes.append(_texte(ex, ey + 2.8, a.etiquette, 7.5, ENCRE, True))

    # Boîtes
    for b in plan.boites:
        fond, contour = TEINTES.get(b.teinte, TEINTES["neutre"])
        corps = []
        if b.accent:
            contour = BLEU
        corps.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" rx="6" '
                     'fill="%s" stroke="%s" stroke-width="%.1f"/>'
                     % (b.x, b.y, b.w, b.h, fond, contour, 2.6 if b.accent else 1.1))
        y = b.y + dsp.MARGE_INT + dsp.T_TITRE
        for ligne in b.lignes_titre:
            corps.append(_texte(b.cx, y, ligne, dsp.T_TITRE, ENCRE, True))
            y += dsp.T_TITRE * dsp.INTERLIGNE
        for ligne in b.lignes_sous:
            corps.append(_texte(b.cx, y + 1, ligne, dsp.T_SOUS, GRIS))
            y += dsp.T_SOUS * dsp.INTERLIGNE
        for ligne in b.lignes_note:
            corps.append(_texte(b.cx, y + 1, ligne, dsp.T_NOTE, GRIS))
            y += dsp.T_NOTE * dsp.INTERLIGNE
        bloc = "".join(corps)
        cible = modele.lien_sur(b.lien)
        if cible:
            bloc = '<a xlink:href=%s target="_top">%s</a>' % (quoteattr(cible), bloc)
        p.append(bloc)

    p.extend(etiquettes)

    # Légende et pied
    y = plan.hauteur - dsp.H_PIED + 8
    x = dsp.MARGE_PAGE
    for teinte, libelle in plan.legende:
        fond, contour = TEINTES.get(teinte, TEINTES["neutre"])
        p.append('<rect x="%.2f" y="%.2f" width="10" height="10" rx="2" '
                 'fill="%s" stroke="%s" stroke-width="0.9"/>'
                 % (x, y - 8, fond, contour))
        p.append(_texte(x + 14, y, libelle, 8, GRIS, False, "start"))
        # À la MESURE, comme le PDF : une avance à l'estime
        # rouvrait exactement la divergence que `mesure` existe pour fermer.
        x += 22 + mesure.largeur(libelle, 8)
    if plan.pied:
        p.append(_texte(plan.largeur - dsp.MARGE_PAGE, y, plan.pied, 7.5,
                        GRIS, False, "end"))
    p.append("</svg>")
    return "".join(p)
