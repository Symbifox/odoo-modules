from odoo import fields, models


class BfTimeOfDayUserPref(models.Model):
    _name = "bf.time.of.day.user_pref"
    _description = "Personal time slot preference"
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
        string="Time slot",
    )
    override_time = fields.Float(
        string="My time",
        required=True,
        help="Replaces the time suggested by the administrator for this slot.",
    )

    _sql_constraints = [
        (
            "user_tod_uniq",
            "unique(user_id, time_of_day_id)",
            "Only one preference per time slot and per user.",
        ),
    ]
