from odoo import fields, models

from .res_users import PARAM_CTRL_K_STAR


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_universal_search_ctrl_k_star = fields.Boolean(
        string="Ctrl+K opens universal search",
        config_parameter=PARAM_CTRL_K_STAR,
        help="Instance default: the command palette opens on universal "
             "search (* prefix) rather than on the Odoo commands. Each "
             "user can choose otherwise in their preferences.",
    )
