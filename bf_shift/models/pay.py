import base64
import csv
import io
from datetime import datetime, time, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..lib import engine
from .tools import post, to_local, tz_of


def code_labels(env):
    return {
        "REG": env._("Regular hours"),
        "OT": env._("Overtime"),
        "DT": env._("Double time"),
        "BANKIN": env._("Overtime to the bank"),
        "BANKOUT": env._("Time taken from the bank"),
        "LEAVE": env._("Paid leave or holiday"),
        "CBTOP": env._("Call-back minimum, top-up"),
        "ONCALL": env._("On call at home"),
        "HOLIND": env._("Holiday indemnity"),
    }


class BfShiftPayPeriod(models.Model):
    """Coded hours of a pay period, ready for the payroll service.

    The module computes hours, overtime and premiums; it does not compute
    deductions. The export is a CSV any payroll service can map.
    """

    _name = "bf.shift.pay.period"
    _description = "Shift pay period"
    _inherit = ["mail.thread"]
    _order = "date_from desc, id desc"

    name = fields.Char(required=True)
    date_from = fields.Date("From", required=True)
    date_to = fields.Date("To", required=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company,
                                 required=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    agreement_ids = fields.Many2many("bf.shift.agreement", string="Working conditions",
                                     help="Empty: every employee with shifts in the period.")
    state = fields.Selection([("draft", "Draft"), ("computed", "Computed"),
                              ("exported", "Exported")], default="draft", required=True,
                             tracking=True)
    line_ids = fields.One2many("bf.shift.pay.line", "period_id", string="Lines")
    total_hours = fields.Float(compute="_compute_totals")
    total_amount = fields.Monetary(compute="_compute_totals")
    computed_at = fields.Datetime(readonly=True)
    export_attachment_id = fields.Many2one("ir.attachment", readonly=True)
    missing_rate_count = fields.Integer(compute="_compute_totals",
                                        string="Employees without an hourly rate")

    @api.depends("line_ids.hours", "line_ids.amount")
    def _compute_totals(self):
        for rec in self:
            worked = rec.line_ids.filtered(lambda l: l.code in ("REG", "OT", "DT"))
            rec.total_hours = sum(worked.mapped("hours"))
            rec.total_amount = sum(rec.line_ids.mapped("amount"))
            rec.missing_rate_count = len(rec.line_ids.employee_id.sudo().filtered(
                lambda e: not e.shift_hourly_rate))

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for rec in self:
            if rec.date_to < rec.date_from:
                raise ValidationError(_("The period ends before it starts."))

    def write(self, vals):
        # Once exported, the period is what payroll received: only reopening
        # (back to draft) and the chatter may touch it.
        exported = self.filtered(lambda p: p.state == "exported")
        if exported and any(k != "state" and not k.startswith(("message_", "activity_"))
                            for k in vals):
            raise UserError(_("An exported period is locked: reopen it first."))
        return super().write(vals)

    def unlink(self):
        if any(rec.state == "exported" for rec in self):
            raise UserError(_("An exported period is kept."))
        return super().unlink()

    # ------------------------------------------------------------------

    def _holidays(self, employee, first, last):
        """Public holidays: company-wide time off (no resource) in the
        employee's working calendar or in none."""
        emp = employee.sudo()
        tz = tz_of(emp, self.env)
        start = datetime.combine(first, time(0, 0))
        end = datetime.combine(last + timedelta(days=1), time(0, 0))
        leaves = self.env["resource.calendar.leaves"].sudo().search([
            ("resource_id", "=", False),
            ("date_from", "<", end + timedelta(days=1)),
            ("date_to", ">", start - timedelta(days=1)),
            "|", ("calendar_id", "=", False),
            ("calendar_id", "=", emp.resource_calendar_id.id),
            "|", ("company_id", "=", False), ("company_id", "=", emp.company_id.id),
        ])
        days = set()
        for leave in leaves:
            d = to_local(leave.date_from, tz).date()
            last_day = to_local(leave.date_to - timedelta(seconds=1), tz).date()
            while d <= last_day:
                if first <= d <= last:
                    days.add(d)
                d += timedelta(days=1)
        return frozenset(days)

    def _employees(self):
        self.ensure_one()
        Assignment = self.env["bf.shift.assignment"].sudo()
        domain = [
            ("state", "!=", "cancelled"),
            ("employee_id", "!=", False),
            ("schedule_id.state", "in", ("published", "closed")),
            ("date", ">=", self.date_from), ("date", "<=", self.date_to),
            ("company_id", "=", self.company_id.id),
        ]
        employees = Assignment.search(domain).employee_id
        employees |= self.env["bf.shift.benefit.event"].sudo().search([
            ("state", "=", "approved"),
            ("date", ">=", self.date_from), ("date", "<=", self.date_to),
        ]).employee_id.filtered(lambda e: e.company_id == self.company_id)
        if self.agreement_ids:
            employees = employees.filtered(
                lambda e: e.sudo().shift_agreement_id in self.agreement_ids)
        return employees

    def action_compute(self):
        for period in self:
            if period.state == "exported":
                raise UserError(_("An exported period is not recomputed."))
            period.line_ids.unlink()
            vals = []
            for employee in period._employees():
                vals += period._employee_lines(employee)
            self.env["bf.shift.pay.line"].create(vals)
            period.write({"state": "computed", "computed_at": fields.Datetime.now()})
        return True

    def _employee_lines(self, employee):
        self.ensure_one()
        emp = employee.sudo()
        tz = tz_of(emp, self.env)
        agreement = emp.shift_agreement_id
        params = agreement._effective_params()
        # Whole weeks around the period, and the 4 weeks before for the
        # holiday indemnity.
        first = engine.week_start_of(self.date_from, params.week_start) - timedelta(days=28)
        last = engine.week_start_of(self.date_to, params.week_start) + timedelta(days=6)
        assignments = self.env["bf.shift.assignment"].sudo().search([
            ("employee_id", "=", emp.id),
            ("state", "!=", "cancelled"),
            ("schedule_id.state", "in", ("published", "closed")),
            ("date", ">=", first), ("date", "<=", last),
        ])
        segments = [a._to_segment(tz, actual=True) for a in assignments]
        rules = agreement._engine_rules() if agreement else []
        holidays = self._holidays(emp, self.date_from, self.date_to)
        lines = engine.compute_pay(segments, agreement._params() if agreement else engine.LNT,
                                   rules=rules, holidays=holidays,
                                   hourly_rate=emp.shift_hourly_rate,
                                   emit_from=self.date_from, emit_to=self.date_to)
        labels = code_labels(self.env)
        out = []
        for line in lines:
            label = line.label or labels.get(line.code, line.code)
            if line.enhanced:
                label = _("%(label)s (enhanced)", label=label)
            ids = [k for k in line.keys if isinstance(k, int)]
            out.append({
                "period_id": self.id,
                "employee_id": emp.id,
                "code": line.code,
                "name": label,
                "hours": line.hours,
                "multiplier": line.multiplier,
                "amount": line.amount,
                "assignment_ids": [(6, 0, ids)],
            })
        events = self.env["bf.shift.benefit.event"].sudo().search([
            ("employee_id", "=", emp.id),
            ("state", "=", "approved"),
            ("date", ">=", self.date_from), ("date", "<=", self.date_to),
        ])
        for event in events:
            out.append({
                "period_id": self.id,
                "employee_id": emp.id,
                "code": event.pay_code,
                "name": dict(event._fields["kind"]._description_selection(self.env))[event.kind],
                "hours": 0.0,
                "multiplier": 0.0,
                "amount": event.amount,
                "benefit_event_id": event.id,
                "taxable_quebec": event.quebec_status,
                "taxable_federal": event.federal_status,
            })
        return out

    def action_export(self):
        self.ensure_one()
        if self.state == "draft":
            raise UserError(_("Compute the period first."))
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(["employee_ref", "employee", "period_from", "period_to", "code",
                         "label", "hours", "multiplier", "amount", "taxable_quebec",
                         "taxable_federal"])
        for line in self.line_ids.sorted(lambda l: (l.employee_id.name or "", l.id)):
            emp = line.employee_id.sudo()
            writer.writerow([
                emp.shift_pay_ref or emp.identification_id or emp.id,
                emp.name,
                self.date_from.isoformat(), self.date_to.isoformat(),
                line.code, line.name,
                "%.2f" % line.hours, "%.2f" % line.multiplier, "%.2f" % line.amount,
                line.taxable_quebec or "", line.taxable_federal or "",
            ])
        data = buf.getvalue().encode("utf-8-sig")
        filename = "shifts-%s-%s.csv" % (self.date_from.isoformat(), self.date_to.isoformat())
        attachment = self.env["ir.attachment"].create({
            "name": filename,
            "raw": data,
            "res_model": self._name,
            "res_id": self.id,
            "mimetype": "text/csv",
        })
        self.write({"state": "exported", "export_attachment_id": attachment.id})
        post(self, body=_("Exported for payroll."), attachment_ids=attachment.ids)
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % attachment.id,
            "target": "self",
        }

    def action_reset(self):
        for period in self:
            period.write({"state": "draft"})
            post(period, body=_("Period reopened: the exported file no longer matches "
                                       "if the lines are recomputed."))
        return True


class BfShiftPayLine(models.Model):
    _name = "bf.shift.pay.line"
    _description = "Shift pay line"
    _order = "period_id, employee_id, id"

    period_id = fields.Many2one("bf.shift.pay.period", required=True, ondelete="cascade",
                                index=True)
    company_id = fields.Many2one(related="period_id.company_id", store=True)
    employee_id = fields.Many2one("hr.employee", required=True, index=True)
    code = fields.Char("Payroll code", required=True)
    name = fields.Char("Label")
    hours = fields.Float()
    multiplier = fields.Float("Rate")
    amount = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one(related="period_id.currency_id")
    assignment_ids = fields.Many2many("bf.shift.assignment", string="Shifts")
    benefit_event_id = fields.Many2one("bf.shift.benefit.event")
    taxable_quebec = fields.Selection(
        [("taxable", "Taxable"), ("not_taxable", "Not taxable"), ("check", "To check")],
        string="Taxable, Québec")
    taxable_federal = fields.Selection(
        [("taxable", "Taxable"), ("not_taxable", "Not taxable"), ("check", "To check")],
        string="Taxable, federal")

    def _check_open(self, periods):
        if any(p.state == "exported" for p in periods):
            raise UserError(_("An exported period is locked: reopen it first."))

    @api.model_create_multi
    def create(self, vals_list):
        self._check_open(self.env["bf.shift.pay.period"].browse(
            [v.get("period_id") for v in vals_list if v.get("period_id")]))
        return super().create(vals_list)

    def write(self, vals):
        self._check_open(self.period_id)
        if vals.get("period_id"):
            self._check_open(self.env["bf.shift.pay.period"].browse(vals["period_id"]))
        return super().write(vals)

    def unlink(self):
        self._check_open(self.period_id)
        return super().unlink()
