# -*- coding: utf-8 -*-
from datetime import date

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import TripCommon


@tagged("post_install", "-at_install")
class TestAssistant(TripCommon):

    def test_the_assistant_prepares_the_whole_trip(self):
        self._rates()
        trip = self._trip(user=self.assistant_user)
        self.assertEqual(trip.employee_id, self.boss)
        receipt = self._expense(user=self.assistant_user, day=date(2026, 11, 3))
        self.assertEqual(receipt.employee_id, self.boss)
        self.assertEqual(receipt.trip_id, trip)
        trip.action_generate_allowances()
        action = trip.action_create_report()
        sheet = self.env["hr.expense.sheet"].with_user(self.assistant_user).browse(action["res_id"])
        sheet.action_submit_sheet()
        self.assertEqual(sheet.state, "submit")
        # Submitted: the assistant still reads it, and no longer changes it.
        self.assertEqual(sheet.with_user(self.assistant_user).read(["name"])[0]["name"], trip.name)
        with self.assertRaises(AccessError):
            receipt.with_user(self.assistant_user).write({"name": "Changé"})

    def test_the_assistant_can_choose_the_boss(self):
        Employee = self.env["hr.employee"].with_user(self.assistant_user)
        allowed = Employee.search([("filter_for_expense", "=", True)])
        self.assertEqual(allowed, self.boss | self.assistant)
        public = self.env["hr.employee.public"].with_user(self.assistant_user).search(
            [("filter_for_expense", "=", True)])
        self.assertEqual(sorted(public.ids), sorted((self.boss | self.assistant).ids))
        self.assertTrue(self.env["hr.expense"].with_user(self.assistant_user).new({}).bf_trip_assists)
        self.assertFalse(self.env["hr.expense"].with_user(self.stranger_user).new({}).bf_trip_assists)

    def test_a_stranger_sees_nothing(self):
        trip = self._trip()
        receipt = self._expense()
        with self.assertRaises(AccessError):
            trip.with_user(self.stranger_user).read(["name"])
        with self.assertRaises(AccessError):
            receipt.with_user(self.stranger_user).read(["name"])
        with self.assertRaises(AccessError):
            self._trip(user=self.stranger_user)
        self.assertFalse(self.Trip.with_user(self.stranger_user).search([]))
        self.assertFalse(self.env["bf.expense.trip.day"].with_user(self.stranger_user).search([]))

    def test_the_traveller_sees_the_trip_an_assistant_made(self):
        trip = self._trip(user=self.assistant_user)
        self.assertEqual(self.Trip.with_user(self.boss_user).search([]), trip)

    def test_search_my_assisted_trips_as_a_plain_user(self):
        trip = self._trip()
        mine = self._trip(employee=self.assistant, name="Le mien")
        found = self.Trip.with_user(self.assistant_user).search([("bf_assisted_by_me", "=", True)])
        self.assertEqual(found, trip)
        others = self.Trip.with_user(self.assistant_user).search([("bf_assisted_by_me", "=", False)])
        self.assertEqual(others, mine)

    def test_an_assistant_who_approves_cannot_approve_the_boss(self):
        self.assistant_user.groups_id |= self.env.ref("hr_expense.group_hr_expense_user")
        trip = self._trip()
        self._expense()
        sheet = self.env["hr.expense.sheet"].browse(trip.action_create_report()["res_id"])
        sheet.action_submit_sheet()
        as_assistant = sheet.with_user(self.assistant_user)
        self.assertFalse(as_assistant.can_approve)
        self.assertIn("you prepare", as_assistant.cannot_approve_reason)
        with self.assertRaises(UserError):
            as_assistant.action_approve_expense_sheets()
        # The same officer approves anyone else's report.
        other = self.env["hr.expense.sheet"].create({
            "name": "Autre", "employee_id": self.expense_employee.id,
            "expense_line_ids": [Command.set(self.create_expense({"name": "x"}).ids)]})
        other.action_submit_sheet()
        self.assertTrue(other.with_user(self.assistant_user).can_approve)

    def test_an_expense_administrator_keeps_the_right(self):
        self.assistant_user.groups_id |= self.env.ref("hr_expense.group_hr_expense_manager")
        trip = self._trip()
        self._expense()
        sheet = self.env["hr.expense.sheet"].browse(trip.action_create_report()["res_id"])
        sheet.action_submit_sheet()
        self.assertTrue(sheet.with_user(self.assistant_user).can_approve)

    def test_an_assistant_removed_loses_the_access(self):
        trip = self._trip()
        self.boss.bf_trip_assistant_user_ids = [Command.clear()]
        with self.assertRaises(AccessError):
            trip.with_user(self.assistant_user).read(["name"])
