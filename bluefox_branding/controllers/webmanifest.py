"""Brand the PWA manifest with the primary company's colors and icon.

Odoo hardcodes ``background_color``/``theme_color`` to Odoo purple and the
icons to ``/web/static/img/odoo-icon-*.png`` in both the main ``/odoo``
manifest and the scoped per-app manifests. The splash screen (Android/Chrome)
is drawn from ``background_color`` + icon, the status bar from ``theme_color``;
overriding them here makes installed PWAs follow the brand
(``res.company.report_brand_primary``, same source as email buttons, and
``res.company.favicon``, same source as the browser tab).

The icon is the most durable surface of the lot: it outlives the session and
sits on the phone's home screen. It is only replaced when the company actually
carries a favicon Chrome can decode — see ``_brand_manifest_icons``.

The primary company is resolved via ``base.main_company``, NOT by sequence
order: archived "(ANCIEN)" companies share the lowest sequence on some
tenants, and the route is public so ``env.company`` is the public user's.
"""

import json

from odoo import http
from odoo.http import request
from odoo.addons.web.controllers.webmanifest import WebManifest

from .app_icons import (
    APP_ICON_SIZES,
    menu_icon_path,
    module_icon_svg,
    read_icon,
    render_app_icon,
)


def _brand_company():
    company = request.env.ref('base.main_company', raise_if_not_found=False)
    return company.sudo() if company else None


def _brand_colors():
    """(background, theme): splash background = brand dark, status bar = brand primary."""
    company = _brand_company()
    if company is None:
        return '#714B67', '#714B67'
    return (
        company.report_brand_dark or '#714B67',
        company.report_brand_primary or '#714B67',
    )


def _is_stock_odoo_icons(icons):
    """True when the manifest still carries Odoo's own icons.

    The scoped per-app manifest serves the APP's icon
    (``<module>/static/description/icon.svg``) when it has one, and falls back
    to the Odoo icon otherwise. Only the fallback is ours to replace —
    overwriting a real app icon would make every installed app look alike.
    """
    return bool(icons) and all(
        (icon.get('src') or '').startswith('/web/static/img/odoo-icon')
        for icon in icons
    )


def _apply_brand(manifest):
    background, theme = _brand_colors()
    manifest['background_color'] = background
    manifest['theme_color'] = theme
    company = _brand_company()
    if company is not None and _is_stock_odoo_icons(manifest.get('icons')):
        icons = company._brand_manifest_icons()
        if icons:
            manifest['icons'] = list(icons)
    return manifest


class BrandedWebManifest(WebManifest):

    def _get_webmanifest(self):
        return _apply_brand(super()._get_webmanifest())

    def scoped_app_manifest(self, app_id, path, app_name=''):
        # The values are inline in the parent route body, so patch its JSON
        # response rather than duplicating the manifest construction.
        response = super().scoped_app_manifest(app_id, path, app_name=app_name)
        try:
            manifest = json.loads(response.get_data(as_text=True))
        except ValueError:
            return response
        response.set_data(json.dumps(_apply_brand(manifest)))
        return response

    def _app_icon_source(self, app_id):
        """Bytes of the best icon for ``app_id``, or ``None``.

        The application's own menu tile comes first, because that is the image
        its user already associates with the app, and on this fleet most tiles
        are drawn from a shared icon module rather than from the module that
        owns the menu. The module's own ``icon.png`` is the fallback for an app
        with no usable tile.
        """
        candidate = menu_icon_path(request.env, app_id)
        source = read_icon(candidate) if candidate else None
        if source is None:
            source = read_icon("%s/static/description/icon.png" % app_id)
        return source

    def _get_scoped_app_icons(self, app_id):
        """The application's own icon, re-rendered square, not a shared fallback.

        Odoo only looks for an ``icon.svg``. No bf_* module has one, so every
        Symbifox app installed as a PWA wore the same fallback icon.

        A module that really does carry an SVG keeps Odoo's own handling.
        """
        # The tenant's own menu tile wins over a module's stock SVG. On this
        # fleet the tile is what carries the brand: `project` ships an SVG, so
        # deferring to it put Odoo's purple icon on the home screen while the
        # app tile in the menu showed the Symbifox one.
        if not menu_icon_path(request.env, app_id) and module_icon_svg(app_id):
            return super()._get_scoped_app_icons(app_id)
        if self._app_icon_source(app_id) is None:
            return super()._get_scoped_app_icons(app_id)
        icons = []
        for size in APP_ICON_SIZES:
            base = "/bluefox_branding/app_icon/%s/%s.png" % (app_id, size)
            icons.append({
                "src": base,
                "sizes": "%sx%s" % (size, size),
                "type": "image/png",
                "purpose": "any",
            })
            icons.append({
                "src": base + "?purpose=maskable",
                "sizes": "%sx%s" % (size, size),
                "type": "image/png",
                "purpose": "maskable",
            })
        return icons

    @http.route(
        "/bluefox_branding/app_icon/<string:app_id>/<int:size>.png",
        type="http", auth="public", methods=["GET"], readonly=True,
    )
    def brand_app_icon(self, app_id, size, purpose=None, **kwargs):
        """Serve an application's own icon, squared and sized, for a manifest.

        Public on purpose: the manifest itself is fetched without a session, and
        a browser fetches the icons it names the same way.

        ``size`` is checked against the list this module advertises so the route
        cannot become an image resizer for arbitrary dimensions, and every path
        that reaches the filesystem goes through ``file_path``, which refuses
        anything outside the addons paths.
        """
        if size not in APP_ICON_SIZES:
            raise request.not_found()
        source = self._app_icon_source(app_id)
        if source is None:
            raise request.not_found()
        image = render_app_icon(source, size, maskable=(purpose == "maskable"))
        if image is None:
            raise request.not_found()
        return request.make_response(image, headers=[
            ("Content-Type", "image/png"),
            # The icon only changes when the module is upgraded, and an
            # installed PWA re-reads it lazily anyway.
            ("Cache-Control", "public, max-age=86400"),
        ])

    @http.route("/web/service-worker.js", type="http", auth="public",
                methods=["GET"], readonly=True)
    def service_worker(self):
        """Widen the worker's maximum scope so scoped apps can claim one.

        Odoo serves this script with ``Service-Worker-Allowed: /odoo``, which is
        the ceiling for any registration of it. A scoped application lives under
        ``/scoped_app/<path>``, outside that ceiling, so it could not be
        controlled by a worker at all: no offline page, and nothing for the
        installability check to interrogate.

        The header only raises the ceiling. What a worker actually controls is
        the scope it registers with, and the only registrations are Odoo's own
        ``/odoo`` and ours on ``/scoped_app`` (see ``scoped_app_worker.js``).
        The public website and portal register none and stay uncontrolled.

        Same widening, same reason as ``bf_contact_enrichment``'s ``/scan/sw.js``.
        """
        response = super().service_worker()
        response.headers["Service-Worker-Allowed"] = "/"
        return response
