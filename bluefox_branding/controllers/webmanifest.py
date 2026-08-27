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

from odoo.http import request
from odoo.addons.web.controllers.webmanifest import WebManifest


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
