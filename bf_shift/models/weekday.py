from odoo import fields, models


class BfShiftWeekday(models.Model):
    _name = "bf.shift.weekday"
    _description = "Day of the week"
    _order = "sequence, code"

    name = fields.Char(required=True, translate=True)
    code = fields.Integer(required=True, help="Python weekday: Monday = 0, Sunday = 6.")
    sequence = fields.Integer(default=10)

    _sql_constraints = [
        ("code_unique", "unique(code)", "Each day of the week exists once."),
    ]
