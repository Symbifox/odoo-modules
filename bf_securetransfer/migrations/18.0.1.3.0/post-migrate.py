"""Apply the en_CA email-template translations on upgrade (noupdate templates
are skipped by the standard translation import — see hooks.apply_email_translations).
18.0.1.3.0 adds mail_template_secure_message (the forced-OTP notification)."""
from odoo.addons.bf_securetransfer.hooks import apply_email_translations


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    apply_email_translations(env)
