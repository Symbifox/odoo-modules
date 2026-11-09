# -*- coding: utf-8 -*-
"""Couleurs des segments : une palette stable, et un texte lisible dessus.

Une roue se regarde de loin, projetée. Deux choses s'y mesurent et ne se devinent
pas : qu'un segment se distingue de son voisin, et que son étiquette se lise sur
sa couleur. La seconde est un calcul, pas un goût : la WCAG donne le contraste de
deux couleurs, et le minimum d'un texte normal est 4,5:1.
"""

import re

# Anthracite et blanc de la maison : les deux seules encres des étiquettes.
DARK_TEXT = "#2d3031"
LIGHT_TEXT = "#ffffff"

# Douze teintes qui se distinguent côte à côte sur un projecteur. Le bleu de la
# maison ouvre la liste. ⚠️ Chacune est posée à une luminosité où l'une des deux
# encres atteint 4,5:1 : quatre teintes choisies d'instinct (#e05263, #8e6fd8,
# #d96a3b, #6c7a89) tombaient entre les deux, trop sombres pour l'encre foncée et
# trop claires pour la blanche, et portaient une étiquette illisible à 3,5:1.
PALETTE = (
    "#29abe2", "#f4a72c", "#7bc043", "#da3246",
    "#815fd4", "#00abab", "#f06c9b", "#3d5a80",
    "#c9b037", "#5bc8af", "#de7f57", "#697685",
)

HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def is_hex(value):
    return bool(value) and bool(HEX_RE.match(value.strip()))


def normalize_hex(value):
    """Rend « #aabbcc » en minuscules, ou False si ce n'est pas une couleur."""
    if not is_hex(value):
        return False
    value = value.strip().lower()
    if len(value) == 4:  # #abc -> #aabbcc
        value = "#" + "".join(c * 2 for c in value[1:])
    return value


def palette_color(index):
    """La couleur du rang donné, la palette tournant indéfiniment."""
    return PALETTE[int(index) % len(PALETTE)]


def _channel(value):
    """Canal sRGB linéarisé, selon la formule de la WCAG."""
    value = value / 255
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color):
    color = normalize_hex(hex_color)
    if not color:
        return 0.0
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(first, second):
    """Le rapport de contraste de la WCAG, de 1:1 (identiques) à 21:1."""
    high, low = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def text_color(hex_color):
    """Celle des deux encres qui contraste le plus avec le fond donné.

    On compare les deux rapports au lieu de franchir un seuil : le seuil qui
    circule (luminance 0,5, ou 0,179 face au noir pur) suppose une encre noire.
    L'encre de la maison est #2d3031, dont le point d'égalité est à 0,238 ; à
    0,5, un segment bleu recevrait du blanc, qui s'y lit à 2,6:1.
    """
    if contrast_ratio(hex_color, LIGHT_TEXT) >= contrast_ratio(hex_color, DARK_TEXT):
        return LIGHT_TEXT
    return DARK_TEXT


# ----------------------------------------------------------------------------
# UNE RAMPE DE MARCHES, POUR LES BLOCS D'UN PODIUM
# ----------------------------------------------------------------------------
# Trois blocs de podium ne sont pas trois couleurs de la palette : c'est un même
# ton qui descend, sinon le podium ressemble à un graphique. Les trois premiers
# blocs du module (#29abe2, #176f92, #0e4a61) avaient été posés à la main ; la
# rampe les calcule, à une unité près, et le fait depuis N'IMPORTE quelle teinte
# — celle de la société, quand elle en a une.
RAMP_FACTEUR = 0.65

# Sous cette luminance, descendre ne distingue plus rien : #000000 assombri
# reste #000000, et les trois blocs seraient identiques. La rampe monte alors.
RAMP_PLANCHER = 0.12

# Le minimum de la WCAG pour un texte normal : chaque marche doit porter une
# encre qui l'atteint. ⚠️ Ce n'est PAS acquis d'une marche calculée : quatre
# teintes de la palette, choisies d'instinct, tombaient entre les deux encres à
# 3,5:1. Une marche qui y tombe est poussée jusqu'à en sortir.
RAMP_CONTRASTE = 4.5


def _to_rgb(hex_color):
    color = normalize_hex(hex_color)
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5)) if color else (0, 0, 0)


def _to_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(canal)))) for canal in rgb)


def darken(hex_color, facteur=RAMP_FACTEUR):
    """Assombrit une couleur en multipliant ses trois canaux."""
    return _to_hex(canal * facteur for canal in _to_rgb(hex_color))


def lighten(hex_color, facteur=RAMP_FACTEUR):
    """Éclaircit une couleur en la mêlant au blanc, dans la même proportion."""
    return _to_hex(canal + (255 - canal) * (1 - facteur) for canal in _to_rgb(hex_color))


def readable(hex_color, vers_le_fonce=True):
    """Pousse une couleur jusqu'à ce qu'une des deux encres y soit lisible.

    Une marche calculée peut tomber entre les deux encres, trop sombre pour
    l'anthracite et trop claire pour le blanc. On la pousse alors dans le sens de
    la rampe, par petits pas, au lieu de la remplacer en silence par une couleur
    de la palette : la teinte de la société reste la sienne, elle ne fait que
    s'assombrir (ou s'éclaircir) jusqu'à porter un texte.
    """
    couleur = normalize_hex(hex_color) or PALETTE[0]
    for _pas in range(24):
        if contrast_ratio(couleur, text_color(couleur)) >= RAMP_CONTRASTE:
            return couleur
        couleur = darken(couleur, 0.92) if vers_le_fonce else lighten(couleur, 0.92)
    return couleur


def brand_ramp(hex_color, marches=3):
    """Les marches d'un même ton, du plus clair au plus foncé.

    La première marche EST la couleur donnée (rendue lisible au besoin) : le
    bloc du premier porte la couleur de la maison, les suivants la même, plus
    foncée. Une teinte presque noire fait monter la rampe au lieu de la
    descendre, parce qu'on ne distingue pas trois noirs.

    Une couleur qui n'est pas une couleur (`False`, « bleu », un code tronqué)
    rend la rampe du bleu mesuré de la palette : c'est le seul endroit où ce
    repli est décidé, et les écrans n'ont pas à le refaire.
    """
    base = normalize_hex(hex_color) or PALETTE[0]
    vers_le_fonce = relative_luminance(base) >= RAMP_PLANCHER
    rampe = [readable(base, vers_le_fonce)]
    for _marche in range(1, max(1, marches)):
        suivante = (darken(rampe[-1]) if vers_le_fonce else lighten(rampe[-1]))
        rampe.append(readable(suivante, vers_le_fonce))
    return tuple(rampe)
