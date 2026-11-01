"""Recharge les catalogues du module par-dessus la base, à chaque changement de version (0.0.0).

Une mise à jour n'écrase jamais une traduction qui existe déjà : un libellé renommé garderait son
ancienne traduction sur les bases installées avant (« À faire à la main » devenu « À faire » en
18.0.1.0.1 restait « To do by hand » en anglais). Le module ne livre aucune donnée traduisible
qu'un client modifierait : écraser est sans risque. Même geste que bf_key_management.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    langs = env["res.lang"].search([("active", "=", True), ("code", "!=", "en_US")]).mapped("code")
    if langs:
        env["ir.module.module"]._load_module_terms(["bf_rental_access"], langs, overwrite=True)
