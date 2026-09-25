from odoo import fields, models

CODES = [
    ("open", "Open shift"),
    ("overlap", "Overlap"),
    ("rest_between", "Rest between shifts"),
    ("meal", "Meal break"),
    ("notice", "Short notice"),
    ("max_24h", "Hours in 24 hours"),
    ("daily_extra", "Long day"),
    ("weekly_max", "Hours in the week"),
    ("weekly_rest", "Weekly rest"),
    ("unavailable", "Unavailable"),
]


class BfShiftWarning(models.Model):
    _name = "bf.shift.warning"
    _description = "Schedule warning"
    _order = "schedule_id, employee_id, id"
    _rec_name = "message"

    schedule_id = fields.Many2one("bf.shift.schedule", required=True, ondelete="cascade",
                                  index=True)
    assignment_id = fields.Many2one("bf.shift.assignment", ondelete="cascade")
    employee_id = fields.Many2one("hr.employee")
    code = fields.Selection(CODES, required=True)
    message = fields.Char(required=True)
