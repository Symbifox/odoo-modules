from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BfShiftTemplate(models.Model):
    _name = "bf.shift.template"
    _description = "Shift template"
    _order = "sequence, hour_from, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char()
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
    hour_from = fields.Float("Starts at", required=True, default=8.0)
    hour_to = fields.Float("Ends at", required=True, default=16.0,
                           help="An end before the start is on the next day (night shift).")
    break_minutes = fields.Integer("Meal break (min)", default=30)
    break_paid = fields.Boolean("Paid break",
                                help="Paid when the employee cannot leave the workstation.")
    kind = fields.Selection([("day", "Day"), ("evening", "Evening"), ("night", "Night"),
                             ("weekend", "Weekend"), ("on_call", "On call")],
                            default="day", required=True)
    job_id = fields.Many2one("hr.job", string="Position")
    department_id = fields.Many2one("hr.department")
    color = fields.Integer()
    duration = fields.Float("Paid hours", compute="_compute_duration")

    @api.depends("hour_from", "hour_to", "break_minutes", "break_paid")
    def _compute_duration(self):
        for tpl in self:
            span = (tpl.hour_to - tpl.hour_from) % 24 or 24.0
            unpaid = 0.0 if tpl.break_paid else (tpl.break_minutes or 0) / 60.0
            tpl.duration = max(0.0, span - unpaid)

    @api.constrains("hour_from", "hour_to", "break_minutes")
    def _check_hours(self):
        for tpl in self:
            if not (0 <= tpl.hour_from < 24 and 0 <= tpl.hour_to <= 24):
                raise ValidationError(_("Hours go from 0 to 24."))
            if tpl.break_minutes < 0:
                raise ValidationError(_("A break cannot be negative."))
