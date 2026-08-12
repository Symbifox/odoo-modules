from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    bf_meeting_dashboard_lookahead_days = fields.Integer(
        string="Horizon — OdJ à préparer (jours)",
        default=90,
        help="Nombre de jours à venir affichés sur le tableau de bord des "
             "rencontres pour les colonnes « OdJ à préparer / envoyer ». "
             "Maximum 90 jours (limite de la vue SQL).",
    )
    bf_meeting_dashboard_lookback_days = fields.Integer(
        string="Horizon — CR en retard (jours)",
        default=180,
        help="Nombre de jours passés affichés sur le tableau de bord des "
             "rencontres pour les colonnes « CR à rédiger / réviser / envoyer ». "
             "Maximum 180 jours (limite de la vue SQL).",
    )

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + [
            "bf_meeting_dashboard_lookahead_days",
            "bf_meeting_dashboard_lookback_days",
        ]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + [
            "bf_meeting_dashboard_lookahead_days",
            "bf_meeting_dashboard_lookback_days",
        ]
