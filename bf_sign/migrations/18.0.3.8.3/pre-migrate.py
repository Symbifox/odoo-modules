"""Force-refresh the signature mail templates (data noupdate="1").

v18.0.3.8.2 switched the header logo to the dark-background logo
(`report_brand_logo`, fallback to the standard logo). The three templates carry
``noupdate="1"`` so a plain ``-u`` never rewrites them; we unlink them here and
the module data load that follows recreates them from the edited XML.
"""
from odoo import SUPERUSER_ID, api

_XMLIDS = (
    "bf_sign.mail_template_sign_request",
    "bf_sign.mail_template_sign_completed",
    "bf_sign.mail_template_sign_refused",
)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xmlid in _XMLIDS:
        tmpl = env.ref(xmlid, raise_if_not_found=False)
        if tmpl:
            tmpl.unlink()
