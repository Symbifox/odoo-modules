from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_persona_ntfy_hook_key = fields.Char(
        string="ntfy hook key (persona degradation)",
        config_parameter="bf_persona.ntfy_hook_key",
        help="Key of the webhook-relay hook (push-server) that receives "
             "the alerts when relationship_health switches to 'degraded' "
             "or tone_summary to 'tense'. Empty = alerts disabled.",
    )
