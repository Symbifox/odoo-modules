"""Pure color helpers, kept free of the ORM so they can be tested alone."""

import hashlib
import re

# $o-colors in web/static/src/scss/secondary_variables.scss (Odoo 18).
# Index 0 is Odoo's "no color".
ODOO_PALETTE = [
    "#A2A2A2", "#EE2D2D", "#DC8534", "#E8BB1D", "#5794DD", "#9F628F",
    "#DB8865", "#41A9A2", "#304BE0", "#EE2F8A", "#61C36E", "#9872E6",
]

# Fallback palette for categories, in order. Okabe-Ito (colour-blind safe, black
# left out) then IBM Carbon's categorical colors, each taken in their published
# order so that neighbours contrast. Okabe-Ito's vermillion and bluish green sit
# last because they read as the status red and green.
DEFAULT_PALETTE = [
    "#E69F00", "#56B4E9", "#0072B2", "#F0E442", "#CC79A7", "#D55E00", "#009E73",
    "#6929C4", "#1192E8", "#005D5D", "#9F1853", "#FA4D56", "#570408", "#198038",
    "#002D9C", "#EE538B", "#B28600", "#009D9A", "#012749", "#8A3800", "#A56EFF",
]

HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalize_hex(value):
    """Return ``#RRGGBB`` in upper case, or ``False`` if ``value`` is not a hex color."""
    if not value or not isinstance(value, str):
        return False
    match = HEX_RE.match(value.strip())
    if not match:
        return False
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return "#" + digits.upper()


def hex_to_rgb(value):
    value = normalize_hex(value)
    if not value:
        return None
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def index_to_hex(index, palette=ODOO_PALETTE):
    """Hex of a palette index; ``False`` for 0 or out of range."""
    if isinstance(index, int) and 0 < index < len(palette):
        return palette[index]
    return False


def nearest_index(value, palette=ODOO_PALETTE):
    """Closest palette index (from 1) to a hex color, 0 when there is no color.

    Distance is the "redmean" approximation, close enough to perception for
    picking one of eleven colors.
    """
    rgb = hex_to_rgb(value)
    if rgb is None:
        return 0
    best, best_dist = 0, None
    for index in range(1, len(palette)):
        r2, g2, b2 = hex_to_rgb(palette[index])
        rmean = (rgb[0] + r2) / 2
        dr, dg, db = rgb[0] - r2, rgb[1] - g2, rgb[2] - b2
        dist = (2 + rmean / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rmean) / 256) * db * db
        if best_dist is None or dist < best_dist:
            best, best_dist = index, dist
    return best


def _relative_luminance(rgb):
    def channel(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(first, second):
    l1 = _relative_luminance(hex_to_rgb(first))
    l2 = _relative_luminance(hex_to_rgb(second))
    light, dark = max(l1, l2), min(l1, l2)
    return (light + 0.05) / (dark + 0.05)


def text_color(background):
    """Black or white, whichever contrasts more with ``background`` (WCAG)."""
    if not normalize_hex(background):
        return False
    if contrast_ratio(background, "#000000") >= contrast_ratio(background, "#FFFFFF"):
        return "#000000"
    return "#FFFFFF"


def stable_pick(key, colors):
    """Same key, same color, across processes and restarts (no ``hash()``)."""
    if not colors:
        return False
    digest = hashlib.sha1(str(key).encode()).digest()
    return colors[int.from_bytes(digest[:4], "big") % len(colors)]


def mix_white(value, white_share):
    """Blend ``value`` with white; Odoo's agenda paints events at 55 % white."""
    rgb = hex_to_rgb(value)
    if rgb is None:
        return False
    mixed = [round(255 * white_share + c * (1 - white_share)) for c in rgb]
    return "#%02X%02X%02X" % tuple(mixed)


def least_used(palette, used):
    """First palette color used the fewest times in ``used`` (a list of hex).

    The next doctor gets a color nobody has yet; once every color is taken, the
    rarest one, in palette order. Unlike a hash, nothing already given moves.
    """
    if not palette:
        return False
    counts = {}
    for value in used:
        value = normalize_hex(value)
        if value:
            counts[value] = counts.get(value, 0) + 1
    return min(palette, key=lambda color: (counts.get(normalize_hex(color), 0), palette.index(color)))
