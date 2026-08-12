"""Bridge setting: automatic detractor ticket."""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_cx_auto_ticket = fields.Boolean(
        string="Ticket automatique pour les détracteurs",
        config_parameter="bf_cx.auto_ticket",
        help="En plus de l'activité de suivi, créer automatiquement un "
             "ticket helpdesk (équipe Plaintes) pour chaque détracteur ou "
             "note insatisfaite.",
    )
