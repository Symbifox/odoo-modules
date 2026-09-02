"""Per-application PWA icons for the scoped app manifests.

Odoo's ``_get_scoped_app_icons`` looks for ``<module>/static/description/icon.svg``
and falls back to Odoo's own icon when there is none. Not one of the bf_* modules
ships an SVG, so every Symbifox application installed to a home screen landed on
the same fallback and they were indistinguishable from each other.

The icon each module *does* ship is a PNG, and it is unusable as-is: they run
small and off-square (140x138, 140x123, 256x256 across the suite), while a
manifest icon has to be at least 192 and a maskable one is cropped to a shape
inscribed in the square. So the module PNG is re-rendered here, centred on a
square canvas, at the two sizes Android actually asks for.

Rendering happens in a public route rather than at manifest build time: the
manifest must stay retrievable without a session, and the browser fetches the
icons separately anyway.
"""

import io

from odoo.tools.misc import file_open, file_path

try:
    from PIL import Image
except ImportError:  # pragma: no cover - Pillow is an Odoo dependency
    Image = None

# The two sizes Chrome/Android look for. 192 is the one it asks for most often.
APP_ICON_SIZES = (192, 512)

# A maskable icon is cropped to a shape inscribed in the square, so the artwork
# has to stay inside the "safe zone", conventionally 80% of the edge.
MASKABLE_SAFE_RATIO = 0.8

# Same choice Odoo makes in ``scoped_app_icon_png`` for Safari icons: a maskable
# icon must cover the whole square opaquely, or the crop reveals transparency.
MASKABLE_BACKGROUND = (255, 255, 255, 255)


def module_icon_svg(app_id):
    """Relative path of a module's own SVG icon, or ``''``.

    SVG is left to Odoo's own handling: this host has no SVG rasteriser, and an
    SVG needs no resizing to satisfy the manifest.
    """
    candidate = "%s/static/description/icon.svg" % app_id
    try:
        file_path(candidate)
    except (FileNotFoundError, ValueError):
        return ""
    return candidate


def module_icon_png_bytes(app_id):
    """Raw bytes of a module's own PNG icon, or ``None``."""
    try:
        path = file_path("%s/static/description/icon.png" % app_id)
    except (FileNotFoundError, ValueError):
        return None
    with file_open(path, "rb") as handle:
        return handle.read()


def render_app_icon(source, size, maskable=False):
    """Centre ``source`` on a square ``size``x``size`` PNG canvas.

    ``maskable`` shrinks the artwork into the safe zone and fills the canvas
    opaquely, which is what makes the platform's crop safe.
    """
    if Image is None or not source:
        return None
    image = Image.open(io.BytesIO(source)).convert("RGBA")
    inner = int(size * MASKABLE_SAFE_RATIO) if maskable else size
    # thumbnail() only ever shrinks, and these icons are smaller than 192, so
    # the box is resized explicitly to preserve the aspect ratio in both
    # directions.
    ratio = min(inner / image.width, inner / image.height)
    box = (max(1, round(image.width * ratio)), max(1, round(image.height * ratio)))
    image = image.resize(box, Image.LANCZOS)
    background = MASKABLE_BACKGROUND if maskable else (255, 255, 255, 0)
    canvas = Image.new("RGBA", (size, size), background)
    canvas.paste(image, ((size - box[0]) // 2, (size - box[1]) // 2), image)
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


def menu_icon_path(env, app_id):
    """Path of the icon the application's own tile shows, or ``''``.

    Not the same thing as ``<module>/static/description/icon.png``. On this
    fleet, 21 of the 54 root menus draw their tile from a shared icon module
    (`symbifox_icons`) rather than from the module that owns the menu, so the
    file that actually represents the app to its user lives elsewhere. The home
    screen should show what the app tile shows.

    Read with ``sudo`` because the manifest route is public; nothing more
    sensitive than an icon path is read.
    """
    data = env["ir.model.data"].sudo().search([
        ("model", "=", "ir.ui.menu"),
        ("module", "=", app_id),
    ], limit=200)
    if not data:
        return ""
    menus = env["ir.ui.menu"].sudo().browse(data.mapped("res_id")).exists()
    for menu in menus:
        if menu.parent_id or not menu.web_icon or "," not in menu.web_icon:
            continue
        module, _, path = menu.web_icon.partition(",")
        candidate = "%s/%s" % (module.strip(), path.strip())
        try:
            file_path(candidate)
        except (FileNotFoundError, ValueError):
            continue
        return candidate
    return ""


def read_icon(candidate):
    """Bytes of an addons-relative icon path, or ``None``."""
    try:
        path = file_path(candidate)
    except (FileNotFoundError, ValueError):
        return None
    with file_open(path, "rb") as handle:
        return handle.read()
