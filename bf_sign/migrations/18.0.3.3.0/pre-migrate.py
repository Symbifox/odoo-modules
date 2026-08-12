"""Force-refresh the signature mail templates (data noupdate="1").

The three invitation/completion/refusal templates carry ``noupdate="1"`` so a
plain ``-u`` never rewrites them. v18.0.3.3.0 adds a title line to each header
band. We unlink the records here (pre-migrate); the module data load that
follows during the same ``-u`` recreates them from the edited XML — fresh
records with no stale ``fr_CA``/``en_CA`` jsonb slots to resync.
"""
from odoo import SUPERUSER_ID, api

_XMLIDS = (
    "bf_sign.mail_template_sign_request",
    "bf_sign.mail_template_sign_completed",
    "bf_sign.mail_template_sign_refused",
)


def migrate(cr, version):
    if not version:
        # Fresh install: XML already carries the titled bodies, nothing to do.
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xmlid in _XMLIDS:
        tmpl = env.ref(xmlid, raise_if_not_found=False)
        if tmpl:
            tmpl.unlink()
