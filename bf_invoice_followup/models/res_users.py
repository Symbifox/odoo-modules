from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    bf_followup_digest = fields.Boolean(
        string="Recevoir le récap des recevables en retard",
        help="Si coché, vous recevez par courriel le récapitulatif des factures "
             "clients en retard, selon la fréquence choisie.",
    )
    bf_followup_digest_interval = fields.Selection(
        [
            ("7", "Chaque semaine (7 jours)"),
            ("14", "Aux deux semaines (14 jours)"),
            ("30", "Chaque mois (30 jours)"),
        ],
        string="Fréquence du récap",
        default="14",
    )
    bf_followup_digest_last_sent = fields.Datetime(
        string="Dernier récap envoyé",
        readonly=True,
        copy=False,
    )

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + [
            "bf_followup_digest",
            "bf_followup_digest_interval",
            "bf_followup_digest_last_sent",
        ]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + [
            "bf_followup_digest",
            "bf_followup_digest_interval",
        ]
