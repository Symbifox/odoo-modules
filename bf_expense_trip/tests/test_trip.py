# -*- coding: utf-8 -*-
from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import TripCommon


@tagged("post_install", "-at_install")
class TestTrip(TripCommon):

    def test_one_line_per_day(self):
        trip = self._trip()
        self.assertEqual(trip.day_ids.mapped("date"),
                         [date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)])
        self.assertTrue(all(trip.day_ids.mapped("dinner")))
        self.assertFalse(any(trip.day_ids.mapped("private_lodging")))

    def test_moving_the_dates_keeps_the_boxes_of_the_days_kept(self):
        trip = self._trip()
        trip.day_ids.filtered(lambda d: d.date == date(2026, 11, 3)).lunch = False
        trip.write({"date_start": date(2026, 11, 3), "date_end": date(2026, 11, 5)})
        self.assertEqual(trip.day_ids.mapped("date"),
                         [date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5)])
        self.assertEqual(trip.day_ids.mapped("lunch"), [False, True, True])

    def test_dates_are_checked(self):
        with self.assertRaises(ValidationError):
            self._trip(start=date(2026, 11, 5), end=date(2026, 11, 4))
        with self.assertRaises(ValidationError):
            self._trip(start=date(2026, 1, 1), end=date(2026, 6, 1))

    def test_collect_takes_the_loose_expenses_of_the_trip_only(self):
        before = self._expense(day=date(2026, 11, 1))
        within = self._expense(day=date(2026, 11, 3))
        other = self._expense(day=date(2026, 11, 3), employee=self.assistant)
        reported = self._expense(day=date(2026, 11, 3))
        self.env["hr.expense.sheet"].create({
            "name": "Déjà", "employee_id": self.boss.id,
            "expense_line_ids": [(6, 0, reported.ids)]})
        trip = self._trip()
        trip.action_collect_expenses()
        self.assertEqual(trip.expense_ids, within)
        self.assertFalse(before.trip_id or other.trip_id or reported.trip_id)

    def test_a_new_expense_finds_its_trip(self):
        trip = self._trip()
        expense = self._expense(day=date(2026, 11, 3))
        self.assertEqual(expense.trip_id, trip)
        outside = self._expense(day=date(2026, 11, 9))
        self.assertFalse(outside.trip_id)

    def test_two_trips_on_the_same_day_choose_none(self):
        self._trip()
        self._trip(name="Autre")
        self.assertFalse(self._expense(day=date(2026, 11, 3)).trip_id)

    def test_an_expense_cannot_join_someone_elses_trip(self):
        trip = self._trip()
        with self.assertRaises(ValidationError):
            self._expense(employee=self.assistant, trip_id=trip.id)

    def test_create_report_makes_one_report_with_the_trip_analytic(self):
        trip = self._trip(analytic_distribution={str(self.analytic_account_1.id): 100})
        plain = self._expense(day=date(2026, 11, 2))
        own = self._expense(day=date(2026, 11, 3),
                            analytic_distribution={str(self.analytic_account_2.id): 100})
        action = trip.action_create_report()
        sheet = self.env["hr.expense.sheet"].browse(action["res_id"])
        self.assertEqual(sheet.trip_id, trip)
        self.assertEqual(sheet.name, trip.name)
        self.assertEqual(sheet.expense_line_ids, plain | own)
        self.assertEqual(plain.analytic_distribution, {str(self.analytic_account_1.id): 100})
        self.assertEqual(own.analytic_distribution, {str(self.analytic_account_2.id): 100})
        self.assertEqual(trip.state, "reported")
        with self.assertRaises(UserError):
            trip.action_create_report()

    def test_company_paid_expenses_go_in_their_own_report(self):
        trip = self._trip()
        self._expense(day=date(2026, 11, 2))
        self._expense(day=date(2026, 11, 3), payment_mode="company_account")
        action = trip.action_create_report()
        self.assertEqual(len(self.env["hr.expense.sheet"].search(action["domain"])), 2)
        self.assertEqual(trip.sheet_count, 2)

    def test_state_follows_the_reports(self):
        trip = self._trip()
        self._expense(day=date(2026, 11, 2))
        self.assertEqual(trip.state, "draft")
        sheet = self.env["hr.expense.sheet"].browse(trip.action_create_report()["res_id"])
        self.assertEqual(trip.state, "reported")
        sheet.action_submit_sheet()
        sheet._do_approve()
        sheet.action_sheet_move_post()
        self.assertEqual(trip.state, "done")
        # A receipt found later puts the trip back in preparation.
        self._expense(day=date(2026, 11, 4), trip_id=trip.id)
        self.assertEqual(trip.state, "draft")

    def test_a_reported_trip_is_not_deleted(self):
        trip = self._trip()
        self._expense()
        trip.action_create_report()
        with self.assertRaises(UserError):
            trip.unlink()

    def test_deleting_a_trip_keeps_the_receipts(self):
        self._rates()
        trip = self._trip()
        receipt = self._expense()
        trip.action_generate_allowances()
        allowances = trip.expense_ids.filtered("bf_trip_allowance")
        self.assertTrue(allowances)
        trip.unlink()
        self.assertTrue(receipt.exists())
        self.assertFalse(receipt.trip_id)
        self.assertFalse(allowances.exists())

    def test_mileage_from_the_trip(self):
        full = self.env.ref("hr_expense.expense_product_mileage")
        full.with_company(self.company).standard_price = 0.73
        self.company.bf_mileage_product_id = full
        trip = self._trip()
        action = trip.action_add_mileage()
        wizard = self.env["bf.expense.mileage.wizard"].with_context(action["context"]).create({
            "name": "Maison à l'aéroport", "distance_km": 42})
        made = self.env["hr.expense"].search(wizard.action_create()["domain"])
        self.assertEqual(made.trip_id, trip)
        self.assertEqual(made.employee_id, self.boss)
        self.assertEqual(made.date, self.start)
