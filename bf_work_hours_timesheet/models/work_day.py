from odoo import api, fields, models


class WorkDay(models.Model):
    """The hours the person entered, beside the reconstructed hours.

    Two numbers for two uses, by design: a timesheet attributes
    effort to work, where the time of several agents running in parallel can
    be entered one after the other; the reconstruction measures presence. No
    alert when one exceeds the other, and neither replaces the other.
    """

    _inherit = "bf.work.day"

    timesheet_hours = fields.Float(string="Entered hours", readonly=True, digits=(16, 2),
                                   help="Timesheet lines of that day, as entered.")

    @api.model
    def _bf_extra_values(self, user, day):
        values = super()._bf_extra_values(user, day)
        groups = self.env["account.analytic.line"].sudo()._read_group(
            [("user_id", "=", user.id), ("date", "=", day), ("project_id", "!=", False)],
            aggregates=["unit_amount:sum"],
        )
        values["timesheet_hours"] = groups[0][0] or 0.0 if groups else 0.0
        return values
