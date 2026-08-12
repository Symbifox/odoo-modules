"""1.1.0: rebranded bilingual rating email (noupdate template refresh)."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.bf_cx_sign.hooks import post_init_hook

    post_init_hook(env)
