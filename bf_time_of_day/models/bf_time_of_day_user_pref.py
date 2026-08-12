from odoo import fields, models


class BfTimeOfDayUserPref(models.Model):
    _name = "bf.time.of.day.user_pref"
    _description = "Préférence personnelle de plage horaire"
    _rec_name = "time_of_day_id"

    user_id = fields.Many2one(
        "res.users",
        required=True,
        ondelete="cascade",
        default=lambda self: self.env.user,
    )
    time_of_day_id = fields.Many2one(
        "bf.time.of.day",
        required=True,
        ondelete="cascade",
        string="Plage horaire",
    )
    override_time = fields.Float(
        string="Mon heure",
        required=True,
        help="Remplace l'heure suggérée par l'admin pour cette plage.",
    )

    _sql_constraints = [
        (
            "user_tod_uniq",
            "unique(user_id, time_of_day_id)",
            "Une seule préférence par plage horaire et par utilisateur.",
        ),
    ]
