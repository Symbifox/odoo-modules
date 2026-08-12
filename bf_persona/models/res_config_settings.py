from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_persona_ntfy_hook_key = fields.Char(
        string="Clé hook ntfy (dégradation persona)",
        config_parameter="bf_persona.ntfy_hook_key",
        help="Clé du hook webhook-relay (push-server) qui reçoit les alertes "
             "lorsque relationship_health bascule en 'degraded' ou tone_summary "
             "en 'tense'. Vide = alertes désactivées.",
    )
