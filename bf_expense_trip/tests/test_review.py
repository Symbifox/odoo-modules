# -*- coding: utf-8 -*-
"""Paths that would let an assistant who also approves expenses approve or undo their
traveller's report, and the integrity checks of trips and folios; one test each."""
from datetime import date

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import TripCommon


@tagged("post_install", "-at_install")
class TestReview(TripCommon):

    def _submitted_boss_report(self):
        trip = self._trip()
        self._expense()
        sheet = self.env["hr.expense.sheet"].browse(trip.action_create_report()["res_id"])
        sheet.action_submit_sheet()
        return trip, sheet

    def _approver_assistant(self):
        self.assistant_user.groups_id |= self.env.ref("hr_expense.group_hr_expense_team_approver")
        return self.env["hr.expense.sheet"].with_user(self.assistant_user)

    def test_s1_no_report_created_already_approved(self):
        Sheet = self._approver_assistant()
        expense = self._expense(user=self.assistant_user)
        with self.assertRaisesRegex(UserError, "cannot approve"):
            Sheet.create({"name": "Déjà approuvée", "employee_id": self.boss.id,
                          "approval_state": "approve",
                          "expense_line_ids": [Command.set(expense.ids)]})
        self.assertFalse(expense.sheet_id)

    def test_s1_not_the_manager_of_the_boss_report(self):
        Sheet = self._approver_assistant()
        expense = self._expense(user=self.assistant_user)
        with self.assertRaisesRegex(UserError, "manager"):
            Sheet.create({"name": "Moi", "employee_id": self.boss.id,
                          "user_id": self.assistant_user.id,
                          "expense_line_ids": [Command.set(expense.ids)]})
        _trip, sheet = self._submitted_boss_report()
        with self.assertRaisesRegex(UserError, "manager"):
            sheet.with_user(self.assistant_user).write({"user_id": self.assistant_user.id})

    def test_s1_a_plain_draft_report_is_still_fine(self):
        Sheet = self._approver_assistant()
        expense = self._expense(user=self.assistant_user)
        sheet = Sheet.create({"name": "Brouillon", "employee_id": self.boss.id,
                              "expense_line_ids": [Command.set(expense.ids)]})
        sheet.action_submit_sheet()
        self.assertEqual(sheet.approval_state, "submit")

    def test_s2_no_reset_of_an_approved_boss_report(self):
        self._approver_assistant()
        _trip, sheet = self._submitted_boss_report()
        sheet._do_approve()
        as_assistant = sheet.with_user(self.assistant_user)
        self.assertFalse(as_assistant.can_reset)
        with self.assertRaises(UserError):
            as_assistant.action_reset_expense_sheets()
        self.assertEqual(sheet.approval_state, "approve")
        self.assertTrue(sheet.account_move_ids)

    def test_s2_a_submitted_report_can_still_go_back_to_draft(self):
        self._approver_assistant()
        _trip, sheet = self._submitted_boss_report()
        sheet.with_user(self.assistant_user).action_reset_expense_sheets()
        self.assertEqual(sheet.state, "draft")

    def test_s4_a_report_cannot_point_to_someone_elses_trip(self):
        trip = self._trip()
        mine = self._expense(employee=self.assistant, day=date(2026, 10, 1))
        sheet = self.env["hr.expense.sheet"].create({
            "name": "La mienne", "employee_id": self.assistant.id,
            "expense_line_ids": [Command.set(mine.ids)]})
        with self.assertRaises(ValidationError):
            sheet.trip_id = trip

    def test_m2_a_company_paid_folio_keeps_its_personal_charges(self):
        expense = self._expense(name="Hôtel", amount=120.0, product_id=self.hotel.id,
                                payment_mode="company_account")
        wizard = self.env["bf.expense.folio.wizard"].with_context(active_id=expense.id).create(
            {"expense_id": expense.id})
        wizard.line_ids = [Command.clear(),
                           Command.create({"name": "Chambre", "amount": 100.0,
                                           "product_id": self.hotel.id}),
                           Command.create({"name": "Minibar", "amount": 20.0, "personal": True})]
        with self.assertRaisesRegex(UserError, "company paid"):
            wizard.action_split()
        self.assertEqual(expense.total_amount_currency, 120.0)

    def test_m3_new_dates_remove_the_allowances(self):
        self._rates()
        trip = self._trip()
        trip.action_generate_allowances()
        trip.date_end = date(2026, 11, 3)
        self.assertFalse(trip.expense_ids.filtered("bf_trip_allowance"))
        self.assertEqual(len(trip.day_ids), 2)

    def test_m3_reported_allowances_freeze_the_dates(self):
        self._rates()
        trip = self._trip()
        trip.action_generate_allowances()
        trip.action_create_report()
        with self.assertRaisesRegex(UserError, "before changing the dates"):
            trip.date_end = date(2026, 11, 3)

    def test_m3_the_report_takes_what_the_days_say_now(self):
        self._rates()
        trip = self._trip()
        trip.action_generate_allowances()
        trip.day_ids.write({"dinner": False})
        sheet = self.env["hr.expense.sheet"].browse(trip.action_create_report()["res_id"])
        self.assertEqual(round(sheet.total_amount, 2), 3 * (10 + 20 + 5))

    def test_m3_no_day_outside_the_trip(self):
        trip = self._trip()
        with self.assertRaises(ValidationError):
            self.env["bf.expense.trip.day"].create({"trip_id": trip.id, "date": date(2026, 12, 25)})
        with self.assertRaises(ValidationError):
            trip.day_ids[0].date = date(2026, 12, 25)

    def test_m4_an_allowance_is_not_a_folio(self):
        self._rates()
        trip = self._trip()
        trip.action_generate_allowances()
        allowance = trip.expense_ids.filtered("bf_trip_allowance")[0]
        wizard = self.env["bf.expense.folio.wizard"].with_context(active_id=allowance.id).create(
            {"expense_id": allowance.id})
        with self.assertRaisesRegex(UserError, "allowance"):
            wizard.action_split()

    def test_d1_a_report_made_outside_the_trip_counts(self):
        trip = self._trip()
        expense = self._expense()
        self.assertEqual(expense.trip_id, trip)
        self.env["hr.expense.sheet"].create({
            "name": "Faite à côté", "employee_id": self.boss.id,
            "expense_line_ids": [Command.set(expense.ids)]})
        self.assertEqual(trip.state, "reported")

    def test_d2_no_trip_for_someone_one_cannot_choose(self):
        stranger = self.env["hr.employee"].search([("user_id", "=", self.stranger_user.id)])
        with self.assertRaises(AccessError):
            self._trip(employee=stranger, user=self.boss_user)
        trip = self._trip(employee=self.boss, user=self.boss_user)
        with self.assertRaises(AccessError):
            trip.with_user(self.boss_user).employee_id = stranger
        self.assertEqual(trip.employee_id, self.boss)


@tagged("post_install", "-at_install")
class TestReviewTwo(TripCommon):
    """More paths of the same families."""

    def _boss_sheet(self, approve=False):
        trip = self._trip()
        self._expense()
        sheet = self.env["hr.expense.sheet"].browse(trip.action_create_report()["res_id"])
        sheet.action_submit_sheet()
        if approve:
            sheet._do_approve()
        return trip, sheet

    def test_f1_no_unapproving_by_a_plain_write(self):
        self.assistant_user.groups_id |= self.env.ref("hr_expense.group_hr_expense_user")
        _trip, sheet = self._boss_sheet(approve=True)
        for vals in ({"approval_state": False}, {"approval_state": "draft"}, {"state": "draft"}):
            with self.assertRaisesRegex(UserError, "cannot approve"):
                sheet.with_user(self.assistant_user).write(vals)
        self.assertEqual(sheet.approval_state, "approve")

    def test_f2_no_moving_a_colleague_sheet_to_the_boss_approved(self):
        self.assistant_user.groups_id |= (self.env.ref("hr_expense.group_hr_expense_user")
                                          | self.env.ref("account.group_account_user"))
        colleague = self.create_expense({"name": "Collègue"})
        sheet = self.env["hr.expense.sheet"].create({
            "name": "Collègue", "employee_id": self.expense_employee.id,
            "expense_line_ids": [Command.set(colleague.ids)]})
        sheet.action_submit_sheet()
        boss_expense = self._expense(day=date(2026, 10, 2))
        with self.assertRaisesRegex(UserError, "cannot approve"):
            sheet.with_user(self.assistant_user).write({
                "employee_id": self.boss.id, "approval_state": "approve",
                "expense_line_ids": [Command.set(boss_expense.ids)]})
        self.assertEqual(sheet.employee_id, self.expense_employee)

    def test_f6_a_trip_whose_expenses_are_reported_elsewhere_stays(self):
        trip = self._trip()
        expense = self._expense()
        self.env["hr.expense.sheet"].create({
            "name": "À côté", "employee_id": self.boss.id,
            "expense_line_ids": [Command.set(expense.ids)]})
        with self.assertRaises(UserError):
            trip.unlink()

    def test_f7_one_allowance_taken_out_does_not_block_the_report(self):
        self._rates()
        trip = self._trip()
        trip.action_generate_allowances()
        sheet = self.env["hr.expense.sheet"].browse(trip.action_create_report()["res_id"])
        first = sheet.expense_line_ids.filtered("bf_trip_allowance")[0]
        sheet.expense_line_ids = [Command.unlink(first.id)]
        action = trip.action_create_report()
        self.assertEqual(self.env["hr.expense.sheet"].browse(action["res_id"]).expense_line_ids, first)

    def test_the_assistant_edits_the_boss_draft_report(self):
        trip = self._trip()
        self._expense()
        sheet = self.env["hr.expense.sheet"].browse(trip.action_create_report()["res_id"])
        self.assertTrue(sheet.with_user(self.assistant_user).is_editable)
        sheet.action_submit_sheet()
        sheet.invalidate_recordset(["is_editable"])
        self.assertFalse(sheet.with_user(self.assistant_user).is_editable)

    def test_the_assistant_splits_a_folio_in_the_boss_draft_report(self):
        expense = self._expense(name="Hôtel", amount=120.0, product_id=self.hotel.id)
        self.env["hr.expense.sheet"].create({
            "name": "Hôtel", "employee_id": self.boss.id,
            "expense_line_ids": [Command.set(expense.ids)]})
        wizard = self.env["bf.expense.folio.wizard"].with_user(self.assistant_user).with_context(
            active_id=expense.id).create({"expense_id": expense.id})
        wizard.line_ids = [Command.clear(),
                           Command.create({"name": "Chambre", "amount": 100.0,
                                           "product_id": self.hotel.id}),
                           Command.create({"name": "Minibar", "amount": 20.0, "personal": True})]
        wizard.action_split()
        self.assertEqual(expense.total_amount_currency, 100.0)
