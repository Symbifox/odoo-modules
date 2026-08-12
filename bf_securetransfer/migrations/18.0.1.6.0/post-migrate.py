"""Re-apply the en_CA email-template translations after the upgrade.

pre-migrate deleted mail_template_secure_message so the data load recreated it
from the reworked XML (FR source). apply_email_translations then rebuilds its
en_CA slot from the .po term map — the same self-healing pass the earlier
migrations run for the noupdate templates."""
from odoo.addons.bf_securetransfer.hooks import apply_email_translations


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    apply_email_translations(env)
