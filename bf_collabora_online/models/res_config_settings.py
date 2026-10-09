from odoo import fields, models

from ..utils.cache_decouverte import TTL_DEFAUT


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_cool_decouverte_ttl = fields.Integer(
        string="Discovery lifetime (seconds)",
        default=TTL_DEFAUT,
        config_parameter="bf_collabora.decouverte_ttl",
        help="The Collabora server's discovery file is kept in memory for "
             "this long. 0 downloads it again at each opening, as the "
             "upstream connector does.",
    )

    def action_bf_vider_cache_decouverte(self):
        self.env["bf.collabora.helper"].vider_cache_decouverte()
        return True
