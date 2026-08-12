"""Reposer les traductions après le rafraîchissement des gabarits.

Le pre-migrate a supprimé les gabarits périmés ; la charge des données vient de
les recréer depuis le XML, donc leur créneau `en_CA` est vide. `overwrite=True`
sur le catalogue du module (les pages publiques et les vues backend gagnent
elles aussi des chaînes en 1.17.0), puis `apply_email_translations` pour les
gabarits `noupdate`, que l'import standard ne voit pas.

Idempotente.
"""
from odoo.addons.bf_securetransfer.hooks import apply_email_translations


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    module = env["ir.module.module"].sudo().search(
        [("name", "=", "bf_securetransfer")], limit=1)
    if module:
        module._update_translations(overwrite=True)
    apply_email_translations(env)
