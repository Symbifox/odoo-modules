"""Shared brand-color replacement logic.

Used by both ``mail.mail`` (at send time) and ``mail.render.mixin`` (at render
time) to swap Odoo's legacy purple for the active company's brand primary.

Implemented as plain module-level functions (NOT an Odoo/Python base class):
adding a foreign base class to an Odoo model breaks Odoo's ``cls.__bases__``
reassignment during registry setup ("object layout differs"). Each model keeps
a thin delegating method that calls these helpers, so the actual logic and the
OLD_COLORS list live in exactly one place.
"""

import re

from markupsafe import Markup

# Old Odoo default colors to replace with the active company's brand primary.
# NB: '#714B67' is both listed here AND the default value of
# res.company.report_brand_primary — for a company left on the default the
# replacement is a harmless no-op (purple -> same purple).
OLD_COLORS = [
    '#875A7B',  # Odoo purple (hex)
    '#875a7b',  # lowercase
    'rgb(135,90,123)',  # Odoo purple (rgb)
    'rgb(135, 90, 123)',  # with spaces
    '#714B67',  # Another Odoo purple variant
    '#714b67',  # lowercase
]


def get_brand_button_color(env):
    """Soft-coded brand primary from res.company (owned by bluefox_branding since 18.0.2)."""
    return (env.company.report_brand_primary or '#714B67')


def get_brand_button_color_rgb(env):
    """rgb() variant of brand primary, derived from the hex value."""
    h = get_brand_button_color(env).lstrip('#')
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f'rgb({r},{g},{b})'
    except (ValueError, IndexError):
        return 'rgb(41,171,226)'


def replace_button_colors(env, html_content):
    """Replace old Odoo button colors with the active company's brand colors.

    Markup in, Markup out: ``re.sub()`` and ``Markup.replace()`` both return a
    plain ``str``, which would silently strip the safe-HTML marking. A caller
    like the mass-mailing layout then feeds that ``str`` to ``<t t-out="body"/>``,
    and QWeb escapes the whole body — the recipient sees ``&lt;table&gt;`` instead
    of the email. Work on a plain str internally, restore the type on the way out.
    """
    if not html_content:
        return html_content

    was_markup = isinstance(html_content, Markup)
    result = str(html_content)
    brand_color = get_brand_button_color(env)
    brand_color_rgb = get_brand_button_color_rgb(env)
    for old_color in OLD_COLORS:
        if old_color.startswith('#'):
            pattern = re.compile(re.escape(old_color), re.IGNORECASE)
            result = pattern.sub(brand_color, result)
        else:
            result = result.replace(old_color, brand_color_rgb)
    return Markup(result) if was_markup else result


# ---------------------------------------------------------------- contraste
# L'accent de marque sert de FOND sous du texte blanc (boutons primaires,
# pastilles, barres de progression). Mesuré le 2026-09-11 : #29ABE2 sous du
# blanc rend 2,62:1, très en dessous du seuil AA de 4,5, et l'accent orange
# d'une seconde société 2,96:1. Ce n'est pas un défaut du mode sombre : la même
# paire rend la même chose en mode clair.
#
# ⚠️ Un assombrissement GLOBAL serait faux : trois des sept accents en
# service passent déjà (le violet d'Odoo rend 7,23:1, un vert sourd 5,47), et les
# noircir ne ferait que les abîmer. La variante se calcule donc par marque, et
# une marque qui passe déjà n'est pas touchée.

_SEUIL_AA = 4.5


def _canal_lineaire(valeur):
    v = valeur / 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _luminance(rgb):
    r, v, b = (_canal_lineaire(c) for c in rgb)
    return 0.2126 * r + 0.7152 * v + 0.0722 * b


def _contraste(rgb_a, rgb_b):
    a, b = _luminance(rgb_a), _luminance(rgb_b)
    haut, bas = (a, b) if a > b else (b, a)
    return (haut + 0.05) / (bas + 0.05)


def _en_rgb(hexa):
    """Hex -> (r, v, b), ou None si la valeur n'est pas lisible."""
    h = (hexa or '').strip().lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None


def assombrir_pour_texte_blanc(hexa, seuil=_SEUIL_AA):
    """La même couleur si elle porte déjà du texte blanc, sinon la plus CLAIRE
    de ses versions assombries qui atteint le seuil.

    On descend par pas de 1 % plutôt que de prendre un facteur fixe : le but
    est de rester le plus près possible de la marque, pas d'obtenir un ton
    foncé. Le bleu clair y perd 26 % de luminosité, un bleu moyen 7 %, et trois
    des sept accents en service n'y perdent rien.
    """
    rgb = _en_rgb(hexa)
    if rgb is None:
        return hexa
    blanc = (255, 255, 255)
    if _contraste(rgb, blanc) >= seuil:
        return hexa
    for pourcent in range(99, 0, -1):
        candidat = tuple(c * pourcent / 100.0 for c in rgb)
        if _contraste(candidat, blanc) >= seuil:
            return '#%02x%02x%02x' % tuple(round(c) for c in candidat)
    return '#000000'


def get_brand_button_color_strong(env):
    """Variante de l'accent utilisable comme FOND sous du texte blanc."""
    return assombrir_pour_texte_blanc(get_brand_button_color(env))
