"""Recharge les catalogues du module par-dessus la base, à chaque version (0.0.0).

Même geste que bf_property_access : une mise à jour n'écrase jamais une
traduction déjà en base, donc un libellé renommé garderait l'ancienne. Le
module ne livre aucune donnée traduisible qu'un client modifierait.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    langs = env["res.lang"].search([("active", "=", True), ("code", "!=", "en_US")]).mapped("code")
    if langs:
        env["ir.module.module"]._load_module_terms(
            ["bf_property_access_expedibox"], langs, overwrite=True)
