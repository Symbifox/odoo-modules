"""Les accents de la traduction française rétablis.

🔴 Une mise à jour n'écrase jamais une traduction déjà en base : les libellés français
(champs, menus, vues) gardaient leur forme sans accents. On recharge le catalogue du
module en écrasant, pour les seules langues françaises installées.
"""

from odoo import SUPERUSER_ID, api

MODULE = "bf_nextcloud_browser"


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    langues = [code for code, _nom in env["res.lang"].get_installed() if code.startswith("fr")]
    if langues:
        env["ir.module.module"]._load_module_terms([MODULE], langues, overwrite=True)
