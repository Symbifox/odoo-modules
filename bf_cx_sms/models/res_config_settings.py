"""Bridge settings: SMS survey invitations."""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_cx_sms_invite = fields.Boolean(
        string="Invitations de sondage par SMS",
        config_parameter="bf_cx.sms_invite",
        help="Active le bouton « Inviter par SMS » sur les vagues d'envoi "
             "pour rejoindre les destinataires sans adresse courriel. "
             "Action manuelle seulement (aucun cron), maximum 5 SMS par "
             "clic.",
    )
    bf_cx_sms_line_id = fields.Many2one(
        "sms.archive.line",
        string="Ligne SMS d'envoi",
        config_parameter="bf_cx.sms_line_id",
        domain=[("active", "=", True), ("sms_enabled", "=", True)],
        help="Ligne utilisée pour les invitations par SMS. Vide = première "
             "ligne active (la ligne par défaut est privilégiée).",
    )
