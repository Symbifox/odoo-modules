"""Génère la paire de clés VAPID lors de la MISE À JOUR (le post_init_hook ne
s'exécute qu'à l'installation initiale). Idempotent : ne régénère pas si présente.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["sms.archive.push.subscription"]._ensure_vapid_keys()
