"""Force-refresh the signature mail templates (data noupdate="1").

v18.0.3.7.0 moves the header title beside the logo. The invitation/completion/
refusal templates carry ``noupdate="1"`` so a plain ``-u`` never rewrites them;
we unlink them here (pre-migrate) and the module data load that follows recreates
them from the edited XML — fresh records, no stale jsonb slots.
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
