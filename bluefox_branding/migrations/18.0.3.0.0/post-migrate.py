"""Re-apply branded mail templates after the multi-company de-hardcoding pass.

The branded mail templates and the late-invoice notice (template 141) are
written by ``post_init_hook`` parsing ``data/mail_template_overrides.xml`` and
the ``_LATE_INVOICE_BODY`` string — they are NOT loaded by Odoo's data loader
(the origin templates are noupdate=True), so a plain ``-u`` does not refresh
them. This migration re-runs the hook so existing installs pick up the
tenant-neutral bodies (logo/website/name/tagline/footer now read from
res.company instead of hardcoded Blue Fox values).

No field seeding is needed: Blue Fox already carries its contact line and legal
links in ``res.company.brand_email_footer_html`` (seeded in 18.0.2.0.0), which
the corrected footers honor first. The new ``brand_privacy_url`` /
``brand_terms_url`` fields are left empty so the optional legal-links row stays
hidden on Blue Fox (avoiding a duplicate with the footer HTML); other tenants
fill them via Paramètres → Général → Identité de marque.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    from odoo.addons.bluefox_branding.hooks import post_init_hook
    post_init_hook(env)
    _logger.info("bluefox_branding 18.0.3.0.0: re-applied tenant-neutral mail templates.")
