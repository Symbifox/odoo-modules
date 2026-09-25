from odoo import api, fields, models

MANAGER = "bf_shift.group_shift_manager"
# Own prefetch group: reading an employee's name must not pull these fields.
# A shift manager who is not an HR officer reads hr.employee through its
# public profile, which refuses any private stored field in the batch.
PREFETCH = "bf_shift"


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    shift_agreement_id = fields.Many2one(
        "bf.shift.agreement", string="Working conditions", groups=MANAGER, prefetch=PREFETCH,
        help="Labour standards or collective agreement that applies to this person's shifts.")
    shift_seniority_date = fields.Date("Seniority date", groups=MANAGER, prefetch=PREFETCH)
    shift_hourly_rate = fields.Monetary(
        "Usual hourly rate", currency_field="shift_currency_id", groups=MANAGER, prefetch=PREFETCH,
        help="Used to compute premiums and amounts. Without it, the export carries hours only.")
    shift_currency_id = fields.Many2one(related="company_id.currency_id", groups=MANAGER, prefetch=PREFETCH,
                                        string="Shift currency")
    shift_pay_ref = fields.Char("Payroll number", groups=MANAGER, prefetch=PREFETCH)
    shift_flexible_hours = fields.Boolean(
        "Flexible hours", groups=MANAGER, prefetch=PREFETCH,
        help="Flexible or no fixed hours: the refusal limit is 12 hours per 24 hours instead of 14.")
    shift_availability_required = fields.Boolean(
        "Duties require availability", groups=MANAGER, prefetch=PREFETCH,
        help="The 5-day notice rule does not apply to this person (LNT art. 59.0.1).")
    shift_bank_balance = fields.Float("Time bank (h)", compute="_compute_shift_bank_balance",
                                      groups=MANAGER, prefetch=PREFETCH)

    def _compute_shift_bank_balance(self):
        lines = self.env["bf.shift.pay.line"].sudo().search([
            ("employee_id", "in", self.ids),
            ("period_id.state", "=", "exported"),
            ("code", "in", ("BANKIN", "BANKOUT")),
        ])
        balance = {emp.id: 0.0 for emp in self}
        for line in lines:
            sign = 1.0 if line.code == "BANKIN" else -1.0
            balance[line.employee_id.id] = balance.get(line.employee_id.id, 0.0) + sign * line.hours
        for emp in self:
            emp.shift_bank_balance = balance.get(emp.id, 0.0)

    def _shift_usual_day_hours(self):
        self.ensure_one()
        cal = self.sudo().resource_calendar_id or self.sudo().company_id.resource_calendar_id
        return (cal.hours_per_day if cal else 0.0) or 8.0
