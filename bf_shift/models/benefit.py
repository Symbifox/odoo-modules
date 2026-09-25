from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..lib import benefits, engine
from .tools import check_own, guard_employee_vals, is_shift_manager

EMPLOYEE_FIELDS = {"employee_id", "date", "assignment_id", "kind", "amount", "currency_id",
                   "attachment_ids", "overtime_hours", "employer_requested",
                   "no_transit_or_safety", "parking_exception"}

KINDS = [
    ("overtime_meal", "Overtime meal"),
    ("taxi", "Taxi home"),
    ("parking", "Parking"),
    ("uniform", "Uniform or protective clothing"),
    ("subsidized_meal", "Subsidised meal"),
    ("other", "Other"),
]
STATUS = [
    (benefits.TAXABLE, "Taxable"),
    (benefits.NOT_TAXABLE, "Not taxable"),
    (benefits.CHECK, "To check with a tax specialist"),
]
def reasons(env):
    return {
        "not_requested": env._("the overtime was not requested by the employer"),
        "under_two_hours": env._("fewer than 2 hours of overtime"),
        "three_times": env._("3 times or more this week"),
        "no_receipt": env._("no receipt"),
        "qc_amount": env._("above the reasonable amount set for Québec"),
        "cra_amount": env._("above the CRA limit"),
        "transit_available": env._("public transit available and no safety issue"),
        "parking_exception": env._("parking exception (value cannot be set, or shared spaces)"),
    }


class BfShiftBenefitEvent(models.Model):
    """A dated event that may be a taxable benefit: a meal during overtime,
    a taxi home, parking. The module sorts it; payroll does the deductions."""

    _name = "bf.shift.benefit.event"
    _description = "Benefit event"
    _inherit = ["mail.thread"]
    _order = "date desc, id desc"

    employee_id = fields.Many2one("hr.employee", required=True, index=True,
                                  default=lambda self: self.env.user.employee_id)
    employee_user_id = fields.Many2one("res.users", compute="_compute_user", store=True,
                                       compute_sudo=True)
    company_id = fields.Many2one(related="employee_id.company_id", store=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    assignment_id = fields.Many2one("bf.shift.assignment", string="Shift",
                                    domain="[('employee_id', '=', employee_id)]")
    kind = fields.Selection(KINDS, required=True, default="overtime_meal")
    amount = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one("res.currency", default=lambda self: self.env.company.currency_id)
    attachment_ids = fields.Many2many("ir.attachment", string="Receipts")
    has_receipt = fields.Boolean(compute="_compute_has_receipt", store=True)
    overtime_hours = fields.Float("Overtime (h)")
    employer_requested = fields.Boolean("Overtime requested by the employer")
    no_transit_or_safety = fields.Boolean("No public transit, or safety at risk")
    parking_exception = fields.Boolean("Parking exception")
    times_this_week = fields.Integer(compute="_compute_verdict", store=True)
    quebec_status = fields.Selection(STATUS, "Québec", compute="_compute_verdict", store=True)
    federal_status = fields.Selection(STATUS, "Federal", compute="_compute_verdict", store=True)
    verdict_codes = fields.Char(compute="_compute_verdict", store=True)
    verdict_note = fields.Char("Why", compute="_compute_verdict_note",
                               help="Why, in the reader's language: the reasons are stored as codes.")
    state = fields.Selection([("draft", "Draft"), ("submitted", "Submitted"),
                              ("approved", "Approved"), ("refused", "Refused")],
                             default="draft", required=True, tracking=True)
    pay_code = fields.Char(compute="_compute_pay_code")

    @api.depends("employee_id.user_id")
    def _compute_user(self):
        for rec in self:
            rec.employee_user_id = rec.employee_id.user_id

    @api.depends("attachment_ids")
    def _compute_has_receipt(self):
        for rec in self:
            rec.has_receipt = bool(rec.attachment_ids)

    def _compute_pay_code(self):
        for rec in self:
            rec.pay_code = "BEN_" + (rec.kind or "other").upper()

    @api.onchange("assignment_id")
    def _onchange_assignment(self):
        if self.assignment_id and not self.overtime_hours:
            self.overtime_hours = self.assignment_id.worked_hours
            self.date = self.assignment_id.date

    @api.depends("kind", "amount", "overtime_hours", "employer_requested", "has_receipt",
                 "no_transit_or_safety", "parking_exception", "date", "employee_id", "state")
    def _compute_verdict(self):
        for rec in self:
            emp = rec.employee_id.sudo()
            agreement = emp.shift_agreement_id
            week_start = int(agreement.week_start or 6) if agreement else 6
            count = 1
            if rec.date and rec.employee_id and rec.kind in ("overtime_meal", "taxi"):
                first = engine.week_start_of(rec.date, week_start)
                domain = [
                    ("employee_id", "=", rec.employee_id.id),
                    ("kind", "=", rec.kind),
                    ("state", "!=", "refused"),
                    ("date", ">=", first), ("date", "<=", rec.date),
                ]
                if rec.id:
                    domain.append(("id", "!=", rec.id))
                    others = self.sudo().search(domain)
                    # Events of the same day count in creation order.
                    others = others.filtered(lambda o: o.date < rec.date or o.id < rec.id)
                else:
                    others = self.sudo().search(domain)
                count = len(others) + 1
            v = benefits.classify(
                rec.kind, amount=rec.amount, overtime_hours=rec.overtime_hours,
                employer_requested=rec.employer_requested, times_this_week=count,
                has_receipt=rec.has_receipt,
                qc_reasonable=agreement.qc_meal_reasonable if agreement else 0.0,
                cra_meal_limit=agreement.cra_meal_limit if agreement else 23.0,
                no_transit_or_safety=rec.no_transit_or_safety,
                parking_exception=rec.parking_exception)
            rec.times_this_week = count
            rec.quebec_status = v.quebec
            rec.federal_status = v.federal
            rec.verdict_codes = ",".join(v.reasons) or False

    @api.depends("verdict_codes")
    def _compute_verdict_note(self):
        labels = reasons(self.env)
        for rec in self:
            codes = (rec.verdict_codes or "").split(",")
            rec.verdict_note = "; ".join(labels[c] for c in codes if c in labels) or False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            guard_employee_vals(self.env, vals, EMPLOYEE_FIELDS)
        records = super().create(vals_list)
        check_own(records)
        return records

    def write(self, vals):
        if not is_shift_manager(self.env):
            if any(rec.state != "draft" for rec in self):
                raise UserError(_("Only a draft can be changed; send it with its button."))
            guard_employee_vals(self.env, vals, EMPLOYEE_FIELDS)
        res = super().write(vals)
        check_own(self)
        return res

    def unlink(self):
        if any(rec.state not in ("draft", "refused") for rec in self):
            raise UserError(_("A submitted event is kept: refuse it instead."))
        return super().unlink()

    def action_submit(self):
        for rec in self:
            self.env["bf.shift.assignment"]._check_is_employee_or_manager(rec.employee_id)
            if rec.state != "draft":
                raise UserError(_("Already submitted."))
            rec.sudo().state = "submitted"
        return True

    def action_approve(self):
        self._manager_only()
        self.write({"state": "approved"})
        return True

    def action_refuse(self):
        self._manager_only()
        self.write({"state": "refused"})
        return True

    def action_draft(self):
        self._manager_only()
        self.write({"state": "draft"})
        return True

    def _manager_only(self):
        if not self.env.user.has_group("bf_shift.group_shift_manager"):
            raise UserError(_("Only a shift manager can do this."))
