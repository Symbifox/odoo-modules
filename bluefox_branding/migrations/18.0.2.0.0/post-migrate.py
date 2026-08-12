"""Refresh onboarding step text, seed BF brand defaults, re-apply mail templates.

The onboarding step record `bf_onb_step_settings` lives in a noupdate=1 data file, so
its title/description don't refresh automatically when the file changes. Write the
new strings explicitly so admins see the wider scope of the Branding panel (logo,
font, emails — not just colors).

For the BF tenant specifically, pre-fill `brand_email_tagline` and
`brand_email_footer_html` on the main company so the upgrade is visually transparent
— the strings that used to be hardcoded in the mail layout now live on res.company.
Only writes when fields are empty (non-destructive).
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

_BF_TAGLINE = "Solutions éthiques et souveraines pour vos données."

_BF_FOOTER_HTML = """\
<a href="mailto:service@example.com" style="color:inherit;text-decoration:none;">service@example.com</a>
<span style="color:#D1D5DB;"> &middot; </span>
<a href="tel:+15555555555" style="color:inherit;text-decoration:none;">555-555-5555</a>
<span style="color:#D1D5DB;"> &middot; </span>
<a href="https://example.com" style="color:inherit;text-decoration:none;">example.com</a>
<br/>
<a href="https://example.com/r/politique-de-confidentialite" style="color:#9CA3AF;text-decoration:underline;">Confidentialité</a>
<span style="color:#D1D5DB;"> | </span>
<a href="https://example.com/r/termes-et-conditions" style="color:#9CA3AF;text-decoration:underline;">Conditions</a>"""


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    step = env.ref("bluefox_branding.bf_onb_step_settings", raise_if_not_found=False)
    if step:
        step.write(
            {
                "title": "Configurer l'identité de marque",
                "description": (
                    "Logo, favicon, couleurs, police et textes de courriels brandés — "
                    "tout est regroupé dans Paramètres → Général → Identité de marque."
                ),
                "button_text": "Ouvrir Identité de marque",
                "done_text": "Marque configurée",
            }
        )
        _logger.info("bluefox_branding 18.0.2.0.0: onboarding step text refreshed.")
    else:
        _logger.warning(
            "bluefox_branding 18.0.2.0.0: onboarding step bf_onb_step_settings missing — skipped."
        )

    # Only seed Blue Fox-specific contact/tagline defaults on a Blue Fox tenant.
    # Non-BF tenants installing this module from the public repo configure their
    # own values via Paramètres → Général → Identité de marque.
    bf_company = env.ref("base.main_company", raise_if_not_found=False)
    if bf_company and (bf_company.name or "").startswith("Blue Fox"):
        seed = {}
        if not bf_company.brand_email_tagline:
            seed["brand_email_tagline"] = _BF_TAGLINE
        if not bf_company.brand_email_footer_html:
            seed["brand_email_footer_html"] = _BF_FOOTER_HTML
        if seed:
            bf_company.write(seed)
            _logger.info(
                "bluefox_branding 18.0.2.0.0: seeded BF brand defaults on %s: %s",
                bf_company.name, list(seed.keys()),
            )

    from odoo.addons.bluefox_branding.hooks import post_init_hook
    post_init_hook(env)
