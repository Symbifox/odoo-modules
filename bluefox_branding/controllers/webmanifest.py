"""Brand the PWA manifest with the primary company's colors.

Odoo hardcodes ``background_color``/``theme_color`` to Odoo purple in both the
main ``/odoo`` manifest and the scoped per-app manifests. The splash screen
(Android/Chrome) is drawn from ``background_color`` + icon, the status bar from
``theme_color``; overriding them here makes installed PWAs follow the brand
primary (``res.company.report_brand_primary``, same source as email buttons).

The primary company is resolved via ``base.main_company``, NOT by sequence
order: archived "(ANCIEN)" companies share the lowest sequence on some
tenants, and the route is public so ``env.company`` is the public user's.
"""

import json

from odoo.http import request
from odoo.addons.web.controllers.webmanifest import WebManifest


def _brand_colors():
    """(background, theme): splash background = brand dark, status bar = brand primary."""
    company = request.env.ref('base.main_company', raise_if_not_found=False)
    if company is None:
        return '#714B67', '#714B67'
    company = company.sudo()
    return (
        company.report_brand_dark or '#714B67',
        company.report_brand_primary or '#714B67',
    )


class BrandedWebManifest(WebManifest):

    def _get_webmanifest(self):
        manifest = super()._get_webmanifest()
        background, theme = _brand_colors()
        manifest['background_color'] = background
        manifest['theme_color'] = theme
        return manifest

    def scoped_app_manifest(self, app_id, path, app_name=''):
        # The colors are inline in the parent route body, so patch its JSON
        # response rather than duplicating the manifest construction.
        response = super().scoped_app_manifest(app_id, path, app_name=app_name)
        try:
            manifest = json.loads(response.get_data(as_text=True))
        except ValueError:
            return response
        background, theme = _brand_colors()
        manifest['background_color'] = background
        manifest['theme_color'] = theme
        response.set_data(json.dumps(manifest))
        return response
