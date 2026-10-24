from odoo import fields, models


class WorkWelcome(models.Model):
    """A person who chose to go on without turning anything on, at their first visit.

    Only so that the consent screen is not shown again: it holds no consent
    and reads nothing. Written by the server for the reader only.
    """

    _name = "bf.work.welcome"
    _description = "Working time: first visit answered"
    # bf_impersonate hides these from whoever impersonates the person.
    _gen_scope = "bf_work_hours"

    user_id = fields.Many2one("res.users", required=True, readonly=True, index=True, ondelete="cascade")
    seen_at = fields.Datetime(string="Answered on", required=True, readonly=True, default=fields.Datetime.now)

    _sql_constraints = [
        ("user_unique", "unique(user_id)", "One answer per person."),
    ]
