# -*- coding: utf-8 -*-
"""A business trip: the dossier an assistant keeps for a traveller.

⚠️ Everything the trip does to expenses runs as the person who clicks: the access rules of
Expenses (and this module's rules for assistants) decide what they may attach, create or
report. Nothing here is done in sudo except finding the trip of a new expense.
"""
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.misc import format_date, formatLang

from .bf_expense_allowance_rate import KINDS

MAX_DAYS = 120


class BfExpenseTrip(models.Model):
    _name = "bf.expense.trip"
    _description = "Business trip"
    _inherit = ["mail.thread", "mail.activity.mixin", "analytic.mixin"]
    _order = "date_start desc, id desc"
    _check_company_auto = True

    def _default_employee_id(self):
        return self.env.user.employee_id

    name = fields.Char(string="Trip", required=True, tracking=True)
    employee_id = fields.Many2one(
        "hr.employee", string="Traveller", required=True, tracking=True, index=True,
        default=_default_employee_id, check_company=True,
        domain=[("filter_for_expense", "=", True)])
    company_id = fields.Many2one(
        "res.company", required=True, index=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_start = fields.Date(string="Departure", required=True, tracking=True,
                             default=fields.Date.context_today)
    date_end = fields.Date(string="Return", required=True, tracking=True,
                           default=fields.Date.context_today)
    destination = fields.Char(tracking=True)
    purpose = fields.Text(help="Why the trip is made: what the employer must be able to show.")
    partner_id = fields.Many2one(
        "res.partner", string="Customer", tracking=True, check_company=True,
        help="The customer the trip is made for, if any.")
    day_ids = fields.One2many("bf.expense.trip.day", "trip_id", string="Days")
    expense_ids = fields.One2many("hr.expense", "trip_id", string="Expenses")
    sheet_ids = fields.One2many("hr.expense.sheet", "trip_id", string="Expense reports")
    expense_count = fields.Integer(compute="_compute_counts")
    sheet_count = fields.Integer(compute="_compute_counts")
    total_amount = fields.Monetary(
        string="Total", currency_field="currency_id", compute="_compute_total_amount",
        help="All the expenses of the trip, in the company's currency.")
    bf_assisted_by_me = fields.Boolean(
        compute="_compute_bf_assisted_by_me", search="_search_bf_assisted_by_me",
        string="I prepare it")
    state = fields.Selection(
        [("draft", "In preparation"), ("reported", "Reported"), ("done", "Done")],
        compute="_compute_state", store=True, tracking=True, default="draft",
        help="In preparation: an expense of the trip is not in a report yet. Done: every report "
             "of the trip is posted or paid.")

    @api.depends_context("uid")
    def _compute_bf_assisted_by_me(self):
        for trip in self:
            trip.bf_assisted_by_me = self.env.user in trip.employee_id.sudo().bf_trip_assistant_user_ids

    def _search_bf_assisted_by_me(self, operator, value):
        """⚠️ The assistants field is for HR officers only: searched as the user, it would
        refuse everyone else. The employees are read in sudo, the trips as the user."""
        if operator not in ("=", "!=") or not isinstance(value, bool):
            raise UserError(_("Operation not supported"))
        assisted = self.env["hr.employee"].sudo().search(
            [("bf_trip_assistant_user_ids", "in", self.env.user.id)]).ids
        positive = (operator == "=") == value
        return [("employee_id", "in" if positive else "not in", assisted)]

    @api.depends("expense_ids", "sheet_ids")
    def _compute_counts(self):
        for trip in self:
            trip.expense_count = len(trip.expense_ids)
            trip.sheet_count = len(trip.sheet_ids)

    @api.depends("expense_ids.total_amount")
    def _compute_total_amount(self):
        for trip in self:
            trip.total_amount = sum(trip.expense_ids.mapped("total_amount"))

    @api.depends("expense_ids.sheet_id.state", "sheet_ids.state")
    def _compute_state(self):
        for trip in self:
            # ⚠️ The reports of the trip's expenses count too: a report made from the plain
            # expense list carries no trip, and the trip stayed « In preparation » for ever.
            sheets = (trip.sheet_ids | trip.expense_ids.sheet_id).filtered(
                lambda s: s.state != "cancel")
            if not sheets or trip.expense_ids.filtered(lambda e: not e.sheet_id):
                trip.state = "draft"
            elif all(s.state in ("post", "done") for s in sheets):
                trip.state = "done"
            else:
                trip.state = "reported"

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for trip in self:
            if trip.date_end < trip.date_start:
                raise ValidationError(_("The return cannot come before the departure."))
            if (trip.date_end - trip.date_start).days + 1 > MAX_DAYS:
                raise ValidationError(_(
                    "A trip lasts at most %s days: split a longer stay into several trips.",
                    MAX_DAYS))

    @api.constrains("employee_id", "company_id")
    def _check_expenses_follow(self):
        for trip in self:
            others = trip.expense_ids.filtered(
                lambda e: e.employee_id != trip.employee_id or e.company_id != trip.company_id)
            if others:
                raise ValidationError(_(
                    "The trip has expenses of another employee or company: %s.",
                    ", ".join(others.mapped("name"))))

    # ------------------------------------------------------------------
    # Days
    # ------------------------------------------------------------------
    def _bf_check_traveller(self, employee):
        """The traveller must be someone the user may choose on an expense (oneself, one's
        team, the people one assists). ⚠️ The field's domain is applied by the browser only.
        The record rules refuse it too today (test_d2), and this check does not depend on how
        Odoo orders its rule checks."""
        if self.env.su or self.env.user.has_group("hr_expense.group_hr_expense_user"):
            return
        if not self.env["hr.employee"].search_count(
                [("filter_for_expense", "=", True), ("id", "=", employee.id)], limit=1):
            raise AccessError(_("You cannot prepare a trip for %s.", employee.sudo().name))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("employee_id"):
                self._bf_check_traveller(self.env["hr.employee"].browse(vals["employee_id"]))
        trips = super().create(vals_list)
        trips._bf_sync_days()
        for trip in trips:
            if trip.employee_id.user_id:
                trip.message_subscribe(partner_ids=trip.employee_id.user_id.partner_id.ids)
        return trips

    def write(self, vals):
        if vals.get("employee_id"):
            self._bf_check_traveller(self.env["hr.employee"].browse(vals["employee_id"]))
        dates = {"date_start", "date_end"} & set(vals)
        if dates:
            # ⚠️ The allowances were counted on the old days: once in a report they stay as
            # approved, so the dates stay too; otherwise they go, to be generated again.
            made = self.expense_ids.filtered("bf_trip_allowance")
            if made.filtered("sheet_id"):
                raise UserError(_(
                    "The allowances of this trip are in a report: take them out of the report "
                    "before changing the dates."))
        result = super().write(vals)
        if dates:
            for trip in self:
                stale = trip.expense_ids.filtered("bf_trip_allowance")
                if stale:
                    stale.unlink()
                    trip._message_log(body=_(
                        "The dates changed: the allowances were removed. Generate them again."))
            self._bf_sync_days()
        return result

    def _bf_sync_days(self):
        """One line per day of the trip: the days kept keep their boxes, the new ones are
        all ticked but the night, and the days now outside the trip go."""
        Day = self.env["bf.expense.trip.day"]
        for trip in self:
            wanted = {trip.date_start + timedelta(days=n)
                      for n in range((trip.date_end - trip.date_start).days + 1)}
            trip.day_ids.filtered(lambda d: d.date not in wanted).unlink()
            have = set(trip.day_ids.mapped("date"))
            Day.create([{"trip_id": trip.id, "date": day} for day in sorted(wanted - have)])

    # ------------------------------------------------------------------
    # Expenses
    # ------------------------------------------------------------------
    def _bf_loose_expenses_domain(self):
        self.ensure_one()
        return [
            ("employee_id", "=", self.employee_id.id),
            ("company_id", "=", self.company_id.id),
            ("trip_id", "=", False),
            ("sheet_id", "=", False),
            ("state", "=", "draft"),
            ("date", ">=", self.date_start),
            ("date", "<=", self.date_end),
        ]

    def action_collect_expenses(self):
        self.ensure_one()
        found = self.env["hr.expense"].search(self._bf_loose_expenses_domain())
        found.write({"trip_id": self.id})
        if found:
            message = _("%(count)s expense(s) attached to the trip.", count=len(found))
        else:
            message = _("No expense of %(employee)s dated within the trip is waiting outside a "
                        "report.", employee=self.employee_id.name)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"message": message, "type": "info" if found else "warning",
                       "next": {"type": "ir.actions.act_window_close"}},
        }

    def action_generate_allowances(self):
        """The ticked boxes of the days, as expenses at the company's rates.

        ⚠️ The allowances made before are replaced, unless one is already in a report: whoever
        approves a report has seen its amounts.
        """
        self.ensure_one()
        made = self.expense_ids.filtered("bf_trip_allowance")
        if made.filtered("sheet_id"):
            raise UserError(_(
                "The allowances of this trip are already in a report: take them out of the "
                "report before generating them again."))
        Rate = self.env["bf.expense.allowance.rate"]
        groups = defaultdict(list)
        missing = set()
        for day in self.day_ids.sorted("date"):
            for kind, _label in KINDS:
                if not day[kind]:
                    continue
                rate = Rate._bf_rate_for(self.company_id, kind, day.date)
                if rate:
                    groups[rate].append(day.date)
                else:
                    missing.add(kind)
        labels = dict(Rate._fields["kind"]._description_selection(self.env))
        if missing:
            raise UserError(_(
                "%(company)s has no rate for: %(kinds)s. Enter the rates in Expenses, "
                "Configuration, Allowance rates, or untick these boxes.",
                company=self.company_id.name,
                kinds=", ".join(labels[k] for k in sorted(missing))))
        made.unlink()
        vals_list = []
        for rate, days in sorted(groups.items(), key=lambda g: (g[1][0], g[0].kind)):
            vals_list.append({
                "name": _("%(kind)s allowance: %(count)s × %(amount)s",
                          kind=labels[rate.kind], count=len(days),
                          amount=formatLang(self.env, rate.amount, currency_obj=self.currency_id)),
                "description": ", ".join(format_date(self.env, d) for d in days),
                "employee_id": self.employee_id.id,
                "company_id": self.company_id.id,
                "product_id": rate.product_id.id,
                "quantity": 1,
                "total_amount_currency": rate.amount * len(days),
                # An allowance has no receipt and no tax paid to claim back.
                "tax_ids": [(6, 0, [])],
                "date": days[0],
                "payment_mode": "own_account",
                "trip_id": self.id,
                "bf_trip_allowance": True,
            })
        expenses = self.env["hr.expense"].with_context(
            default_employee_id=self.employee_id.id).create(vals_list)
        if self.analytic_distribution:
            expenses.analytic_distribution = self.analytic_distribution
        return self.action_view_expenses()

    def action_create_report(self):
        """One report for the trip (two when the company paid some of it, as Odoo does)."""
        self.ensure_one()
        allowances = self.expense_ids.filtered("bf_trip_allowance")
        if allowances.filtered(lambda e: not e.sheet_id) and not allowances.filtered("sheet_id"):
            # The boxes may have changed since the allowances were generated: what goes in the
            # report is what the days say now.
            self.action_generate_allowances()
        todo = self.expense_ids.filtered(lambda e: not e.sheet_id and e.state == "draft")
        if not todo:
            raise UserError(_("Every expense of the trip is already in a report."))
        if self.analytic_distribution:
            todo.filtered(lambda e: not e.analytic_distribution).analytic_distribution = (
                self.analytic_distribution)
        vals_list = todo._get_default_expense_sheet_values()
        for vals in vals_list:
            vals["trip_id"] = self.id
            if len(vals_list) == 1 or not vals.get("name"):
                vals["name"] = self.name
        sheets = self.env["hr.expense.sheet"].create(vals_list)
        action = {
            "type": "ir.actions.act_window",
            "res_model": "hr.expense.sheet",
            "name": _("Expense reports"),
            "target": "current",
        }
        if len(sheets) == 1:
            action.update(view_mode="form", res_id=sheets.id)
        else:
            action.update(view_mode="list,form", domain=[("id", "in", sheets.ids)])
        return action

    def action_add_mileage(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.expense.mileage.wizard",
            "name": _("Add mileage"),
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_employee_id": self.employee_id.id,
                "default_company_id": self.company_id.id,
                "default_date": self.date_start,
                "default_bf_trip_id": self.id,
            },
        }

    def action_view_expenses(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "hr.expense",
            "name": _("Expenses"),
            "view_mode": "list,form",
            "domain": [("trip_id", "=", self.id)],
            "context": {"default_trip_id": self.id, "default_employee_id": self.employee_id.id,
                        "default_company_id": self.company_id.id},
        }

    def action_view_sheets(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "hr.expense.sheet",
            "name": _("Expense reports"),
            "view_mode": "list,form",
            "domain": [("trip_id", "=", self.id)],
        }

    @api.ondelete(at_uninstall=False)
    def _unlink_only_in_preparation(self):
        if self.filtered(lambda t: t.sheet_ids or t.expense_ids.sheet_id):
            raise UserError(_("A trip with an expense report cannot be deleted."))

    def unlink(self):
        # The allowances were made by the trip and mean nothing without it; the receipts stay.
        self.expense_ids.filtered("bf_trip_allowance").unlink()
        return super().unlink()


class BfExpenseTripDay(models.Model):
    _name = "bf.expense.trip.day"
    _description = "Day of a business trip"
    _order = "trip_id, date"

    trip_id = fields.Many2one("bf.expense.trip", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="trip_id.company_id", store=True)
    currency_id = fields.Many2one(related="trip_id.currency_id")
    date = fields.Date(required=True)
    breakfast = fields.Boolean(default=True)
    lunch = fields.Boolean(default=True)
    dinner = fields.Boolean(default=True)
    incidentals = fields.Boolean(default=True)
    private_lodging = fields.Boolean(
        string="Private lodging", default=False,
        help="A night with friends or family, without a hotel receipt.")
    amount = fields.Monetary(compute="_compute_amount", currency_field="currency_id",
                             help="The ticked boxes at the rates in force that day.")

    _sql_constraints = [
        ("bf_day_unique", "unique(trip_id, date)", "A trip has one line per day."),
    ]

    @api.constrains("date", "trip_id")
    def _check_within_trip(self):
        """⚠️ Days are made by the trip; one added or moved over RPC outside the dates would be
        paid by Generate allowances."""
        for day in self:
            if not day.trip_id.date_start <= day.date <= day.trip_id.date_end:
                raise ValidationError(_("The day %(day)s is not within the trip \"%(trip)s\".",
                                        day=day.date, trip=day.trip_id.name))

    @api.depends("date", "breakfast", "lunch", "dinner", "incidentals", "private_lodging",
                 "trip_id.company_id")
    def _compute_amount(self):
        Rate = self.env["bf.expense.allowance.rate"]
        for day in self:
            total = 0.0
            if day.date and day.trip_id:
                for kind, _label in KINDS:
                    if day[kind]:
                        total += Rate._bf_rate_for(day.trip_id.company_id, kind, day.date).amount
            day.amount = total
