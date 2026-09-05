from odoo import fields, models

from .res_users import PARAM_CTRL_K_STAR


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_universal_search_ctrl_k_star = fields.Boolean(
        string="Ctrl+K ouvre la recherche universelle",
        config_parameter=PARAM_CTRL_K_STAR,
        help="Défaut de l'instance : la palette de commandes s'ouvre sur la "
             "recherche universelle (préfixe *) plutôt que sur les commandes "
             "Odoo. Chaque usager peut choisir autrement dans ses préférences.",
    )
