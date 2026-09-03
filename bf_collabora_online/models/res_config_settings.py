from odoo import fields, models

from ..utils.cache_decouverte import TTL_DEFAUT


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_cool_decouverte_ttl = fields.Integer(
        string="Durée de vie de la découverte (secondes)",
        default=TTL_DEFAUT,
        config_parameter="bf_collabora.decouverte_ttl",
        help="Le fichier de découverte du serveur Collabora est gardé en mémoire "
             "pendant ce délai. 0 pour le retélécharger à chaque ouverture, "
             "comme le fait le connecteur amont.",
    )

    def action_bf_vider_cache_decouverte(self):
        self.env["bf.collabora.helper"].vider_cache_decouverte()
        return True
