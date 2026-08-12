"""Apply the en_CA email-template translations on upgrade (noupdate templates
are skipped by the standard translation import — see hooks.apply_email_translations)."""
from odoo.addons.bf_securetransfer.hooks import apply_email_translations


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    apply_email_translations(env)
