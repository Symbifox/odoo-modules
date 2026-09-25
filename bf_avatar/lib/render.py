"""Avatar rendering, with no dependency on the ORM.

Every SVG this module produces is assembled from the templates in
``data/styles/*.json`` and from values that ``clean_config`` has checked against
those templates. Nothing a user types ever reaches the markup: a config key or
value that is not in the style is dropped, and a colour must be one of the
style's own palette entries. That is what makes it safe to store the result as
an SVG attachment with sudo.
"""
import colorsys
import hashlib
import json
import os
import re
from functools import lru_cache

from markupsafe import escape

STYLE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "styles")

# Tenant-level choice. "odoo" leaves Odoo's own generator alone.
STYLES = [
    ("odoo", "Odoo default (initial on a random colour)"),
    ("initials", "Initials in the house colours"),
    ("open_peeps", "Character: Open Peeps (hand drawn, in colour)"),
    ("notionists", "Character: Notionists (line art)"),
]
CHARACTER_STYLES = ("open_peeps", "notionists")

# Marks every SVG this module writes, so a later pass can tell it apart from a
# picture somebody uploaded.
MARKER = 'data-bf-avatar="1"'

# Odoo's own generated avatar (avatar_mixin._avatar_generate_svg): one rect, one
# letter, a fixed header. An uploaded SVG never matches this shape.
_ODOO_GENERATED = re.compile(
    r"^<\?xml version='1\.0' encoding='UTF-8' \?>"
    r"<svg height='180' width='180' xmlns='http://www\.w3\.org/2000/svg' "
    r"xmlns:xlink='http://www\.w3\.org/1999/xlink'>"
    r"<rect fill='hsl\([0-9, %]+\)' height='180' width='180'/>"
    r"<text fill='#ffffff' font-size='96' text-anchor='middle' x='90' y='125' "
    r"font-family='sans-serif'>[^<]{1,12}</text></svg>$"
)

# Parts a seeded (default) avatar never draws: expressions and props that are
# fun to pick but odd to be handed. The gamification bridge sells exactly these.
SEED_EXCLUDE = {
    "open_peeps": {
        "head": {"hatBeanie", "hatHip", "mohawk", "mohawk2", "pomp", "bear"},
        "face": {"angryWithFang", "cyclops", "monster", "rage", "veryAngry", "contempt",
                 "fear", "concernedFear", "hectic", "suspicious", "eatingHappy", "blank",
                 "tired", "eyesClosed", "concerned", "old"},
        "accessories": {"eyepatch", "sunglasses", "sunglasses2"},
    },
    "notionists": {
        "hair": {"hat"},
        "body": {"variant%02d" % i for i in range(10, 26)},
        "gesture": None,   # None = the whole slot
        "bodyIcon": None,
    },
}

# How often an optional slot shows up in a seeded avatar, in percent.
SEED_PROBABILITY = {
    "open_peeps": {"accessories": 20, "facialHair": 15, "mask": 0},
    "notionists": {"beard": 10, "glasses": 20, "gesture": 0, "bodyIcon": 0},
}

_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


@lru_cache(maxsize=None)
def load_style(style):
    if style not in CHARACTER_STYLES:
        raise ValueError("Unknown character style: %s" % style)
    with open(os.path.join(STYLE_DIR, "%s.json" % style), encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=None)
def slot_order(style):
    """Slots in drawing order: the body's first, then parts drawn inside a part."""
    data = load_style(style)
    order = re.findall(r"@@slot:(\w+)@@", data["body"])
    for slot in list(order):
        for variant in data["slots"][slot].values():
            for nested in re.findall(r"@@slot:(\w+)@@", variant):
                if nested not in order:
                    order.append(nested)
    return tuple(order)


def optional_slots(style):
    return set(load_style(style)["probabilities"])


def is_generated_svg(raw):
    """True for an SVG that Odoo or this module generated, never for an upload."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return False
    raw = (raw or "").strip()
    if not raw.startswith("<"):
        return False
    return MARKER in raw[:400] or bool(_ODOO_GENERATED.match(raw))


class _Draw:
    """Deterministic chooser: the same seed always gives the same avatar."""

    def __init__(self, seed):
        self.seed = seed or ""
        self.count = 0

    def number(self):
        self.count += 1
        digest = hashlib.sha256(("%s\x1f%d" % (self.seed, self.count)).encode()).digest()
        return int.from_bytes(digest[:8], "big")

    def pick(self, values):
        values = list(values)
        return values[self.number() % len(values)] if values else None

    def chance(self, percent):
        return (self.number() % 100) < percent


def seedable(style, slot):
    variants = list(load_style(style)["slots"][slot])
    excluded = SEED_EXCLUDE.get(style, {}).get(slot, set())
    if excluded is None:
        return []
    return [v for v in variants if v not in excluded]


def seed_config(style, seed):
    data = load_style(style)
    draw = _Draw(seed)
    config = {}
    probabilities = SEED_PROBABILITY.get(style, {})
    for slot in slot_order(style):
        pool = seedable(style, slot)
        if slot in data["probabilities"]:
            if not pool or not draw.chance(probabilities.get(slot, data["probabilities"][slot])):
                config[slot] = None
                continue
        config[slot] = draw.pick(pool) if pool else draw.pick(data["slots"][slot])
    config["colors"] = {name: draw.pick(values) for name, values in sorted(data["colors"].items())}
    return config


def clean_config(style, config, seed=""):
    """Keep only what the style knows; fill the rest from the seed."""
    data = load_style(style)
    base = seed_config(style, seed)
    config = config if isinstance(config, dict) else {}
    cleaned = {}
    for slot in slot_order(style):
        value = config.get(slot, base[slot]) if slot in config else base[slot]
        if value is None and slot in data["probabilities"]:
            cleaned[slot] = None
        elif isinstance(value, str) and value in data["slots"][slot]:
            cleaned[slot] = value
        else:
            cleaned[slot] = base[slot]
    colors = config.get("colors") if isinstance(config.get("colors"), dict) else {}
    cleaned["colors"] = {}
    for name, values in data["colors"].items():
        value = colors.get(name)
        cleaned["colors"][name] = value if value in values else base["colors"][name]
    return cleaned


def parts_of(style, config):
    """(slot, variant) pairs a config draws, for unlock checks."""
    return {(slot, config[slot]) for slot in slot_order(style) if config.get(slot)}


def _hex(value):
    match = _HEX.match(value or "")
    return "#%s" % match.group(1).lower() if match else None


def render_character(style, config, background="#f2f4f7"):
    data = load_style(style)
    config = clean_config(style, config)
    body = data["body"]
    # Two rounds: a part may carry the token of a part drawn inside it.
    for _round in range(2):
        for slot in slot_order(style):
            variant = config.get(slot)
            body = body.replace("@@slot:%s@@" % slot, data["slots"][slot][variant] if variant else "")
    # Slot templates carry their own colour tokens, so replace colours last.
    for name, value in config["colors"].items():
        body = body.replace("@@color:%s@@" % name, "#%s" % value)
    if "@@" in body:
        raise ValueError("Unresolved token in %s avatar" % style)
    background = _hex(background) or "#f2f4f7"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" %s viewBox="%s" fill="none" '
        'shape-rendering="auto"><rect width="100%%" height="100%%" fill="%s"/>%s</svg>'
        % (MARKER, escape(data["viewBox"]), background, body)
    )


def initial_of(name):
    for char in (name or "").strip():
        if char.isalnum():
            return char.upper()
    return "?"


def render_initials(name, background):
    background = _hex(background) or "#1f84af"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" %s viewBox="0 0 180 180">'
        '<rect width="180" height="180" fill="%s"/>'
        '<text fill="#ffffff" font-size="96" text-anchor="middle" x="90" y="125" '
        'font-family="Lexend, Helvetica, Arial, sans-serif">%s</text></svg>'
        % (MARKER, background, escape(initial_of(name)))
    )


# --- Colours -----------------------------------------------------------------

def _luminance(hex_colour):
    rgb = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _hls_hex(h, l, s):
    r, g, b = colorsys.hls_to_rgb(h % 1.0, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def _to_hls(hex_colour):
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hls(r, g, b)


def parse_palette(text):
    return [c for c in (_hex(part.strip()) for part in re.split(r"[\s,;]+", text or "")) if c]


def initials_palette(base_colours):
    """Shades of the house colours that keep white text readable (4.5:1)."""
    shades = []
    for colour in base_colours or ["#1f84af"]:
        h, l, s = _to_hls(colour)
        for shift in (0.0, -0.04, 0.04):
            shade = _hls_hex(h + shift, min(l, 0.42), max(s, 0.35))
            while contrast(shade, "#ffffff") < 4.5:
                h2, l2, s2 = _to_hls(shade)
                shade = _hls_hex(h2, l2 - 0.03, s2)
            if shade not in shades:
                shades.append(shade)
    return shades


def character_palette(base_colours):
    """Pale tints of the house colours, behind dark line art."""
    tints = []
    for colour in base_colours or ["#1f84af"]:
        h, _l, s = _to_hls(colour)
        for shift in (0.0, 0.05):
            tint = _hls_hex(h + shift, 0.88, min(max(s, 0.3), 0.6))
            if tint not in tints:
                tints.append(tint)
    return tints


def pick_colour(palette, seed):
    return _Draw("colour\x1f%s" % seed).pick(palette) if palette else None
