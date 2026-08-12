from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    bf_time_of_day_pref_ids = fields.One2many(
        "bf.time.of.day.user_pref",
        "user_id",
        string="Mes plages horaires",
    )

    def _tod_effective_time(self, time_of_day):
        """Return the effective time (Float HH.MM) the user has set for this slot,
        falling back to the admin-configured default. Returns False if neither is set.

        Used by project.task to mutate `date_deadline` when a slot is picked.
        """
        self.ensure_one()
        if not time_of_day:
            return False
        pref = self.env["bf.time.of.day.user_pref"].search(
            [("user_id", "=", self.id), ("time_of_day_id", "=", time_of_day.id)],
            limit=1,
        )
        if pref:
            return pref.override_time
        return time_of_day.default_time or False
