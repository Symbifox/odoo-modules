# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class HrExpense(models.Model):
    _inherit = "hr.expense"

    trip_id = fields.Many2one(
        "bf.expense.trip", string="Trip", index="btree_not_null", ondelete="set null",
        check_company=True, tracking=True,
        domain="[('employee_id', '=', employee_id), ('state', '=', 'draft')]")
    bf_trip_allowance = fields.Boolean(
        string="Trip allowance", readonly=True, copy=False,
        help="Made by the trip from its days: generating the allowances again replaces it.")
    bf_folio_personal_amount = fields.Monetary(
        string="Personal charges set aside", currency_field="currency_id", readonly=True,
        copy=False, help="Part of the hotel folio the company does not reimburse.")
    bf_trip_assists = fields.Boolean(
        compute="_compute_bf_trip_assists",
        help="The current user prepares the expenses of at least one other employee.")

    @api.depends_context("uid")
    def _compute_bf_trip_assists(self):
        assists = bool(self.env["hr.employee"].sudo().search_count(
            [("bf_trip_assistant_user_ids", "in", self.env.user.id)], limit=1))
        for expense in self:
            expense.bf_trip_assists = assists

    @api.constrains("trip_id", "employee_id", "company_id")
    def _check_trip(self):
        for expense in self.filtered("trip_id"):
            trip = expense.trip_id.sudo()
            if trip.employee_id != expense.employee_id:
                raise ValidationError(_(
                    "The expense \"%(expense)s\" is %(employee)s's, and the trip \"%(trip)s\" is "
                    "%(traveller)s's.", expense=expense.name, employee=expense.employee_id.name,
                    trip=trip.name, traveller=trip.employee_id.name))
            if trip.company_id != expense.company_id:
                raise ValidationError(_(
                    "The expense \"%(expense)s\" and the trip \"%(trip)s\" belong to different "
                    "companies.", expense=expense.name, trip=trip.name))

    @api.model_create_multi
    def create(self, vals_list):
        expenses = super().create(vals_list)
        expenses.filtered(lambda e: not e.trip_id)._bf_trip_attach_new()
        return expenses

    def _bf_trip_attach_new(self):
        """A new expense goes to the trip of its traveller that covers its date, when there is
        exactly one in preparation. Read in sudo: the person who creates the expense (an
        approver, the mail gateway) may not see the trip, and the trip is the traveller's own."""
        Trip = self.env["bf.expense.trip"].sudo()
        for expense in self:
            if not (expense.date and expense.employee_id) or expense.sheet_id:
                continue
            trips = Trip.search([
                ("employee_id", "=", expense.employee_id.id),
                ("company_id", "=", expense.company_id.id),
                ("state", "=", "draft"),
                ("date_start", "<=", expense.date),
                ("date_end", ">=", expense.date),
            ], limit=2)
            if len(trips) == 1:
                expense.sudo().trip_id = trips
