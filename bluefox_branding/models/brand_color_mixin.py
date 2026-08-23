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
