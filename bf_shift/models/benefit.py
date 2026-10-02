from datetime import timedelta

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..lib import benefits, engine
from .tools import check_own, employee_defaults, flag, guard_employee_vals, is_shift_manager

EMPLOYEE_FIELDS = {"employee_id", "date", "assignment_id", "kind", "amount", "currency_id",
                   "attachment_ids", "overtime_hours", "employer_requested",
                   "no_transit_or_safety", "parking_exception", "provision",
                   "meal_cost", "meal_price_paid"}

KINDS = [
    ("overtime_meal", "Overtime meal"),
    ("taxi", "Taxi home"),
    ("parking", "Parking"),
    ("uniform", "Uniform or protective clothing"),
    ("subsidized_meal", "Subsidised meal"),
    ("other", "Other"),
]
PROVISIONS = [
    (benefits.REIMBURSED, "Reimbursed on receipts"),
    (benefits.PROVIDED, "Provided by the employer"),
]
STATUS = [
    (benefits.TAXABLE, "Taxable"),
    (benefits.NOT_TAXABLE, "Not taxable"),
    (benefits.CHECK, "To check with a tax specialist"),
]
def reasons(env):
    return {
        "not_requested": env._("the overtime was not requested by the employer"),
        "under_two_hours": env._("fewer than 2 consecutive hours of overtime planned"),
        "three_times": env._("3 times or more this week"),
        "no_receipt": env._("no receipt"),
        "qc_amount": env._("above the reasonable amount set for Québec"),
        "cra_amount": env._("above the CRA limit"),
        "transit_available": env._("public transit available and no safety issue"),
        "parking_exception": env._("parking exception (value cannot be set, or shared spaces)"),
        "cra_taxi": env._("federal: the trip between home and work is personal travel for the "
                          "CRA; no published exception covers a taxi after overtime"),
        "federal_exemption": env._("federal: not taxable, as justified by the employer"),
        "meal_cost_missing": env._("cost of the food and its preparation not entered"),
        "price_covers_cost": env._("the price paid covers the cost of the food and its "
                                   "preparation"),
        "price_below_cost": env._("the price paid is below the cost of the food and its "
                                  "preparation: the benefit is the difference"),
    }


class BfShiftBenefitEvent(models.Model):
    """A dated event that may be a taxable benefit: a meal during overtime,
    a taxi home, parking. The module sorts it; payroll does the deductions."""

    _name = "bf.shift.benefit.event"
    _description = "Benefit event"
    _inherit = ["mail.thread", "bf.shift.derived"]
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
    amount = fields.Monetary(
        currency_field="currency_id", compute="_compute_amount", store=True, readonly=False,
        help="For a subsidised meal: the benefit, cost of the food and its preparation less "
        "the price paid, computed.")
    currency_id = fields.Many2one("res.currency", default=lambda self: self.env.company.currency_id)
    attachment_ids = fields.Many2many("ir.attachment", string="Receipts")
    has_receipt = fields.Boolean(compute="_compute_has_receipt", store=True)
    overtime_hours = fields.Float(
        "Planned consecutive overtime (h)",
        help="The overtime as planned when the employer asked for it, in one stretch: the "
        "2-hour condition reads the planned length, not the hours finally worked "
        "(LI art. 37.0.3).")
    provision = fields.Selection(
        PROVISIONS, "Meal or taxi", default=benefits.REIMBURSED, required=True,
        help="Provided by the employer (a meal served or ordered, a taxi paid by the employer): "
        "no receipt is needed. Reimbursed: the employee paid, the reimbursement is made on "
        "receipts (LI art. 37.0.3).")
    meal_cost = fields.Monetary(
        "Cost of food and preparation", currency_field="currency_id",
        help="What the meal costs the employer: the food and its preparation, without fixed "
        "costs of the premises. When a third party runs the service, the full price of the "
        "meal.")
    meal_price_paid = fields.Monetary("Price paid by the employee", currency_field="currency_id")
    federal_exemption_reason = fields.Char(
        "Federal exemption, justification",
        help="The taxi home is taxable by default at the CRA. Enter why it is not, for this "
        "event, to record it as not taxable. Shift managers only.")
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

    @api.depends("kind", "employee_id", "date")
    def _compute_display_name(self):
        # Without a name, Odoo would show "bf.shift.benefit.event,21".
        kinds = dict(self._fields["kind"]._description_selection(self.env))
        for rec in self:
            rec.display_name = " — ".join(
                part for part in (kinds.get(rec.kind), rec.employee_id.sudo().name,
                                  rec.date and rec.date.isoformat()) if part)

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

    @api.depends("kind", "meal_cost", "meal_price_paid")
    def _compute_amount(self):
        for rec in self:
            # Without the cost, the value is unknown ("to check"): the amount
            # typed in stays, as on a meal entered before the cost existed.
            if rec.kind == "subsidized_meal" and rec.meal_cost:
                rec.amount = benefits.subsidized_value(rec.meal_cost, rec.meal_price_paid)
            else:
                rec.amount = rec.amount

    @api.onchange("assignment_id")
    def _onchange_assignment(self):
        if self.assignment_id and not self.overtime_hours:
            # Planned, not worked: the 2-hour condition reads the plan.
            self.overtime_hours = self.assignment_id.planned_hours
            self.date = self.assignment_id.date

    @api.depends("kind", "amount", "overtime_hours", "employer_requested", "has_receipt",
                 "no_transit_or_safety", "parking_exception", "date", "employee_id", "state",
                 "provision", "meal_cost", "meal_price_paid", "federal_exemption_reason")
    def _compute_verdict(self):
        for rec in self:
            emp = rec.employee_id.sudo()
            agreement = emp.shift_agreement_id
            week_start = int(agreement.week_start or 6) if agreement else 6
            count = 1
            # Counted as superuser (a stored field computes in sudo), so only
            # for whoever may know it: the employee herself, a manager or the
            # system. ``env.user`` stays the real caller under sudo, and an
            # onchange is anyone's call.
            user = self.env.user
            may_count = user.id == SUPERUSER_ID or emp.user_id == user or \
                user.has_group("bf_shift.group_shift_manager")
            if may_count and rec.date and rec.employee_id and \
                    rec.kind in ("overtime_meal", "taxi"):
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
                parking_exception=rec.parking_exception, provision=rec.provision,
                meal_cost=rec.meal_cost, meal_price_paid=rec.meal_price_paid,
                federal_exemption=bool((rec.federal_exemption_reason or "").strip()))
            rec.times_this_week = count
            rec.quebec_status = v.quebec
            rec.federal_status = v.federal
            rec.verdict_codes = ",".join(v.reasons) or False

    @api.depends("verdict_codes", "federal_exemption_reason")
    def _compute_verdict_note(self):
        labels = reasons(self.env)
        for rec in self:
            codes = (rec.verdict_codes or "").split(",")
            parts = []
            for code in codes:
                if code == "federal_exemption" and (rec.federal_exemption_reason or "").strip():
                    parts.append("%s (%s)" % (labels[code], rec.federal_exemption_reason.strip()))
                elif code in labels:
                    parts.append(labels[code])
            rec.verdict_note = "; ".join(parts) or False

    @api.model
    def default_get(self, fields_list):
        return employee_defaults(self, super().default_get(fields_list), EMPLOYEE_FIELDS)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            guard_employee_vals(self.env, vals, EMPLOYEE_FIELDS)
        for vals in vals_list:
            self._check_receipts(vals)
        records = super().create(vals_list)
        check_own(records)
        records._sync_subsidized_amount()
        return records

    def write(self, vals):
        if not is_shift_manager(self.env):
            if any(rec.state != "draft" for rec in self):
                raise UserError(_("Only a draft can be changed; send it with its button."))
            guard_employee_vals(self.env, vals, EMPLOYEE_FIELDS)
        self._check_receipts(vals)
        res = super().write(vals)
        check_own(self)
        self._sync_subsidized_amount()
        return res

    def _check_receipts(self, vals):
        """Whoever links a receipt uploaded it, and it is not yet tied to
        another record: otherwise any file of the database could be linked to
        an old event and destroyed with it. A manager too: she may not read
        every file either. Receipts already on the event stay acceptable."""
        if self.env.su or "attachment_ids" not in vals:
            return
        ids = set()
        for command in vals["attachment_ids"] or []:
            if isinstance(command, int):
                ids.add(command)
            elif command[0] == 4:
                ids.add(command[1])
            elif command[0] == 6:
                ids.update(command[2] or [])
        ids -= set(self.attachment_ids.ids)
        files = self.env["ir.attachment"].sudo().browse(sorted(ids)).exists()
        mine = set(self.ids)
        if len(files) != len(ids) or any(
                f.create_uid != self.env.user
                or f.res_model not in (False, self._name)
                or (f.res_model == self._name and f.res_id and f.res_id not in mine)
                for f in files):
            raise AccessError(self.env._("Attach only receipts you uploaded yourself."))

    def _sync_subsidized_amount(self):
        """A subsidised meal is worth its cost less the price paid, whatever
        amount was typed in (a value given with the record wins over the
        computed one otherwise). Without the cost, the amount typed in stays."""
        for rec in self.filtered(lambda r: r.kind == "subsidized_meal" and r.meal_cost):
            value = benefits.subsidized_value(rec.meal_cost, rec.meal_price_paid)
            if rec.currency_id.compare_amounts(rec.amount, value) if rec.currency_id \
                    else abs(rec.amount - value) > 1e-9:
                super(BfShiftBenefitEvent, rec.sudo()).write({"amount": value})

    def unlink(self):
        if not flag(self.env, "retention") and \
                any(rec.state not in ("draft", "refused") for rec in self):
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
