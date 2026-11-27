import importlib.util
from datetime import date
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.modules.module import get_module_path
from odoo.tests import tagged

from odoo.addons.bf_route.tests.common import MONDAY, RouteCase


def _migration():
    """The 18.0.1.1.0 migration script, loaded from its file (its path is not a module name)."""
    path = f"{get_module_path('bf_route_expense')}/migrations/18.0.1.1.0/pre-migrate.py"
    spec = importlib.util.spec_from_file_location("bf_route_expense_migrate_1_1_0", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged("post_install", "-at_install")
class TestMileageExpense(RouteCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env["hr.employee"].create({
            "name": "Route Worker", "user_id": cls.worker.id, "company_id": cls.company.id})
        cls.full = cls.env.ref("hr_expense.expense_product_mileage")
        cls.reduced = cls.env.ref("bf_expense_mileage.product_mileage_reduced")
        cls.full.with_company(cls.company).standard_price = 0.73
        cls.reduced.with_company(cls.company).standard_price = 0.67
        cls.company.write({"bf_mileage_product_id": cls.full.id,
                           "bf_mileage_reduced_product_id": cls.reduced.id,
                           "bf_mileage_threshold": 5000})
        cls.truck.bf_route_personal = True

    def finish(self, start=1000, end=1085, day=None):
        day = day or self.make_day()
        self.start(day, odometer=start)
        day.stop_ids.sorted("sequence")[0].with_user(self.worker).app_mark("done", app_key="a")
        day.with_user(self.worker).app_finish(odometer=end)
        return day

    def manual(self, km, when=MONDAY):
        return self.env["hr.expense"].create({
            "name": "By hand", "employee_id": self.employee.id, "product_id": self.full.id,
            "quantity": km, "date": when})

    def test_own_car_gives_a_draft_expense(self):
        day = self.finish()
        expense = day.sudo().expense_ids
        self.assertEqual(len(expense), 1)
        self.assertEqual(expense.employee_id, self.employee)
        self.assertEqual(expense.product_id, self.full)
        self.assertAlmostEqual(expense.quantity, 85.0)
        self.assertEqual(expense.date, MONDAY)
        self.assertEqual(expense.state, "draft")
        self.assertFalse(expense.sheet_id, "the worker reports it, nothing is submitted for them")
        self.assertAlmostEqual(expense.total_amount_currency, 62.05)
        self.assertIn("North shore", expense.name)
        self.assertIn("Alice Café", expense.description)
        self.assertIn("1,000", expense.description)
        self.assertEqual(day.expense_count, 1)
        self.assertIn("85.0 km claimed", day.message_ids[0].body)

    def test_the_worker_sees_their_expense(self):
        day = self.finish()
        self.assertEqual(self.env["hr.expense"].with_user(self.worker).search(
            [("bf_route_day_id", "=", day.id)]), day.sudo().expense_ids)

    def test_company_vehicle_gives_none(self):
        self.truck.bf_route_personal = False
        day = self.finish()
        self.assertFalse(day.sudo().expense_ids)
        with self.assertRaisesRegex(UserError, "own vehicle"):
            day.with_user(self.manager).action_claim_mileage()

    def test_no_odometer_no_expense(self):
        day = self.make_day()
        self.start(day)
        day.with_user(self.worker).app_finish()
        self.assertEqual(day.state, "done")
        self.assertFalse(day.sudo().expense_ids)
        with self.assertRaisesRegex(UserError, "odometer"):
            day.with_user(self.manager).action_claim_mileage()

    def test_threshold_splits_the_day(self):
        self.manual(4990)
        day = self.finish()
        lines = {e.product_id: e.quantity for e in day.sudo().expense_ids}
        self.assertAlmostEqual(lines[self.full], 10.0)
        self.assertAlmostEqual(lines[self.reduced], 75.0)
        self.assertAlmostEqual(sum(day.sudo().expense_ids.mapped("total_amount_currency")), 10 * 0.73 + 75 * 0.67)
        self.assertIn("beyond", day.sudo().expense_ids.filtered(lambda e: e.product_id == self.reduced).name)

    def test_past_the_threshold_all_reduced(self):
        self.manual(6000)
        day = self.finish()
        self.assertEqual(day.sudo().expense_ids.product_id, self.reduced)

    def test_last_year_and_refused_do_not_count(self):
        self.manual(6000, when=date(2025, 12, 31))
        refused = self.manual(6000)
        sheet = self.env["hr.expense.sheet"].create({
            "name": "Refused", "employee_id": self.employee.id, "expense_line_ids": [(6, 0, refused.ids)]})
        sheet.action_submit_sheet()
        sheet._do_refuse("no")
        self.assertEqual(refused.state, "refused")
        day = self.finish()
        self.assertEqual(day.sudo().expense_ids.product_id, self.full)

    def test_threshold_zero_means_full_rate(self):
        self.company.bf_mileage_threshold = 0
        self.manual(6000)
        day = self.finish()
        self.assertEqual(day.sudo().expense_ids.product_id, self.full)

    def test_worker_without_employee_still_finishes(self):
        self.employee.user_id = False
        day = self.finish()
        self.assertEqual(day.state, "done")
        self.assertFalse(day.sudo().expense_ids)
        self.assertIn("has no employee", day.message_ids[0].body, "the office is told why")
        activity = day.activity_ids.filtered(lambda a: "Kilometres not claimed" in (a.summary or ""))
        self.assertEqual(activity.user_id, self.manager, "and the person responsible gets an activity")
        self.assertIn("has no employee", activity.note)
        with self.assertRaisesRegex(UserError, "no employee"):
            day.with_user(self.manager).action_claim_mileage()
        day.with_user(self.manager).write({"odometer_end": 1090})
        self.assertEqual(len(day.activity_ids.filtered(lambda a: "Kilometres not claimed" in (a.summary or ""))), 1,
                         "the office's own corrections give a note, not another activity")

    def test_an_employee_of_another_company_is_not_the_one(self):
        other = self.env["res.company"].create({"name": "Other company", "currency_id": self.company.currency_id.id})
        self.worker.write({"company_ids": [(4, other.id)]})
        self.employee.write({"company_id": other.id})
        day = self.make_day()
        self.start(day, odometer=1000)
        # The phone works in both companies: the employee of the other one must not be taken.
        day.with_user(self.worker).with_context(
            allowed_company_ids=[self.company.id, other.id]).app_finish(odometer=1085)
        self.assertFalse(day.sudo().expense_ids)
        self.assertIn("has no employee", day.message_ids[0].body)

    def test_the_caller_s_context_never_reaches_the_expense(self):
        # sudo() already drops the default_* keys; the others would still get through.
        day = self.finish()
        day.with_user(self.manager).with_context(
            mail_create_nolog=True, default_total_amount_currency=5000).write({"odometer_end": 1100})
        expense = day.sudo().expense_ids
        self.assertAlmostEqual(expense.total_amount_currency, 73.0)
        self.assertTrue(expense.message_ids, "the expense keeps its creation trace")
        # action_finish is public: the worker can call it over RPC with any context.
        self.employee.user_id = False
        day2 = self.make_day(day=MONDAY.replace(day=19))
        self.start(day2, odometer=2000)
        day2.with_user(self.worker).with_context(mail_activity_quick_update=True).action_finish(odometer=2010)
        notified = self.env["mail.message"].search_count([
            ("model", "=", "bf.route.day"), ("res_id", "=", day2.id), ("message_type", "=", "user_notification"),
            ("body", "ilike", "Kilometres not claimed")])
        self.assertEqual(notified, 1, "the person responsible is told, whatever the caller's context")

    def test_office_of_another_company_cannot_claim(self):
        other = self.env["res.company"].create({"name": "Other company", "currency_id": self.company.currency_id.id})
        day = self.finish()
        day.sudo().expense_ids.unlink()
        self.manager.write({"company_ids": [(6, 0, other.ids)], "company_id": other.id})
        with self.assertRaises(AccessError):
            day.with_user(self.manager).with_context(allowed_company_ids=other.ids).action_claim_mileage()
        self.assertFalse(day.sudo().expense_ids)

    def test_only_a_fleet_manager_marks_a_vehicle_personal(self):
        self.truck.bf_route_personal = False
        # The driver may write their vehicle (Fleet rules): only the guard stops them.
        self.truck.driver_id = self.manager.partner_id
        self.truck.with_user(self.manager).write({"license_plate": "ABC 124"})
        with self.assertRaises(AccessError):
            self.truck.with_user(self.manager).write({"bf_route_personal": True})
        self.truck.with_user(self.manager).write({"bf_route_personal": False})
        fleet_manager = self.env["res.users"].create({
            "name": "Fleet boss", "login": "fleet_boss",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id, self.env.ref("fleet.fleet_group_manager").id])]})
        self.truck.with_user(fleet_manager).write({"bf_route_personal": True})
        self.assertTrue(self.truck.bf_route_personal)

    def test_a_negative_draft_does_not_shift_the_threshold(self):
        self.manual(4990)
        self.manual(-100000)
        day = self.finish()
        lines = {e.product_id: e.quantity for e in day.sudo().expense_ids}
        self.assertAlmostEqual(lines[self.full], 10.0)

    def test_a_correction_redoes_the_later_drafts(self):
        self.manual(4900)
        first = self.finish()
        second = self.finish(start=2000, end=2085, day=self.make_day(day=MONDAY.replace(day=19)))
        lines = {e.product_id: e.quantity for e in second.sudo().expense_ids}
        self.assertEqual((lines[self.full], lines[self.reduced]), (15.0, 70.0))
        # A context key cannot stop the redo of the later days.
        first.with_user(self.manager).with_context(bf_route_no_cascade=True).write({"odometer_end": 1100})
        self.assertEqual(first.sudo().expense_ids.product_id, self.full)
        self.assertEqual(second.sudo().expense_ids.product_id, self.reduced, "the later day moves past the threshold")
        self.assertAlmostEqual(sum(second.sudo().expense_ids.mapped("quantity")), 85.0)

    def test_the_redo_leaves_a_draft_the_worker_changed(self):
        self.manual(4900)
        first = self.finish()
        second = self.finish(start=2000, end=2085, day=self.make_day(day=MONDAY.replace(day=19)))
        mine = second.sudo().expense_ids.filtered(lambda e: e.product_id == self.full)
        mine.with_user(self.worker).write({"quantity": 20})
        before = second.sudo().expense_ids
        first.with_user(self.manager).write({"odometer_end": 1100})
        self.assertEqual(second.sudo().expense_ids, before, "edited by hand: left alone")
        self.assertIn("changed by hand", second.message_ids[0].body)

    def test_a_settings_error_never_deletes_a_draft(self):
        day = self.finish()
        expense = day.sudo().expense_ids
        self.reduced.active = False
        day.with_user(self.manager).write({"odometer_end": 1090})
        self.assertTrue(expense.exists(), "a setting fixed later would not bring it back")
        self.assertIn("reduced-rate", day.message_ids[0].body)

    def test_a_day_given_to_someone_else_redoes_the_former_s_later_drafts(self):
        other_employee = self.env["hr.employee"].create({
            "name": "Other", "user_id": self.other.id, "company_id": self.company.id})
        self.manual(4900)
        first = self.finish()
        second = self.finish(start=2000, end=2085, day=self.make_day(day=MONDAY.replace(day=19)))
        self.assertEqual(len(second.sudo().expense_ids), 2)
        first.with_user(self.manager).write({"user_id": self.other.id})
        self.assertEqual(first.sudo().expense_ids.employee_id, other_employee)
        self.assertEqual(second.sudo().expense_ids.product_id, self.full, "the former's year lost 85 km")

    def test_a_day_moved_later_redoes_the_days_it_jumps_over(self):
        self.manual(4900)
        first = self.finish()
        second = self.finish(start=2000, end=2085, day=self.make_day(day=MONDAY.replace(day=19)))
        self.assertEqual(len(second.sudo().expense_ids), 2)
        first.with_user(self.manager).write({"date": MONDAY.replace(day=26)})
        self.assertEqual(second.sudo().expense_ids.product_id, self.full, "no longer counts the moved day")
        lines = {e.product_id: e.quantity for e in first.sudo().expense_ids}
        self.assertEqual((lines[self.full], lines[self.reduced]), (15.0, 70.0))

    def test_a_day_given_to_someone_without_employee_drops_the_former_s_draft(self):
        day = self.finish()
        day.with_user(self.manager).write({"user_id": self.other.id})
        self.assertFalse(day.sudo().expense_ids, "the former no longer drove this day")
        self.assertIn("has no employee", day.message_ids[0].body)

    def test_a_day_moved_moves_its_expense(self):
        day = self.finish()
        day.with_user(self.manager).write({"date": MONDAY.replace(day=13)})
        self.assertEqual(day.sudo().expense_ids.date, MONDAY.replace(day=13))

    def test_a_later_day_does_not_count_before_an_earlier_one(self):
        self.manual(4900)
        later = self.finish(start=2000, end=2085, day=self.make_day(day=MONDAY.replace(day=19)))
        earlier = self.finish()
        self.assertEqual(earlier.sudo().expense_ids.product_id, self.full, "dated before: counts 4,900 only")
        lines = {e.product_id: e.quantity for e in later.sudo().expense_ids}
        self.assertEqual((lines[self.full], lines[self.reduced]), (15.0, 70.0), "redone after the earlier day")

    def test_reduced_rate_product_archived_says_so(self):
        self.reduced.active = False
        day = self.finish()
        self.assertFalse(day.sudo().expense_ids)
        self.assertIn("reduced-rate", day.message_ids[0].body)
        self.company.bf_mileage_threshold = 0
        day.with_user(self.manager).action_claim_mileage()
        self.assertEqual(day.sudo().expense_ids.product_id, self.full)

    def test_a_product_without_rate_claims_nothing(self):
        self.full.with_company(self.company).standard_price = 0
        day = self.finish()
        self.assertFalse(day.sudo().expense_ids)

    def test_a_failing_claim_never_blocks_the_end_of_the_day(self):
        Day = type(self.env["bf.route.day"])
        with patch.object(Day, "_bf_route_create_expenses", side_effect=UserError("boom")):
            day = self.finish()
        self.assertEqual(day.state, "done")
        self.assertFalse(day.sudo().expense_ids)
        self.assertIn("not claimed", day.message_ids[0].body)

    def test_office_corrects_the_odometer(self):
        day = self.finish()
        first = day.sudo().expense_ids
        day.with_user(self.manager).write({"odometer_end": 1100})
        self.assertFalse(first.exists(), "the draft is redone")
        self.assertAlmostEqual(day.sudo().expense_ids.quantity, 100.0)
        same = day.sudo().expense_ids
        day.with_user(self.manager).write({"odometer_end": 1100})
        self.assertEqual(day.sudo().expense_ids, same, "same kilometres, same expense")

    def test_office_sees_the_claim_it_just_changed(self):
        self.manual(4990)
        day = self.finish(end=1005)
        self.assertEqual(day.expense_count, 1)
        self.assertEqual(len(day.sudo().expense_ids), 1)
        day.with_user(self.manager).write({"odometer_end": 1085})
        self.assertEqual(day.expense_count, 2, "the office's own view of the day is not stale")
        self.assertEqual(len(day.sudo().expense_ids), 2)
        day.with_user(self.manager).write({"odometer_end": 1000})
        self.assertEqual(day.expense_count, 0)

    def test_reported_expense_is_never_changed(self):
        day = self.finish()
        expense = day.sudo().expense_ids
        sheet = self.env["hr.expense.sheet"].create({
            "name": "October", "employee_id": self.employee.id, "expense_line_ids": [(6, 0, expense.ids)]})
        sheet.action_submit_sheet()
        day.with_user(self.manager).write({"odometer_end": 1200})
        self.assertEqual(day.sudo().expense_ids, expense)
        self.assertAlmostEqual(expense.quantity, 85.0)
        self.assertIn("after the expense was reported", day.message_ids[0].body)

    def test_office_claims_a_day_finished_before(self):
        self.truck.bf_route_personal = False
        day = self.finish()
        self.truck.bf_route_personal = True
        day.with_user(self.manager).action_claim_mileage()
        self.assertAlmostEqual(day.sudo().expense_ids.quantity, 85.0)

    def test_worker_cannot_claim_from_the_office_button(self):
        day = self.finish()
        with self.assertRaises(AccessError):
            day.with_user(self.worker).action_claim_mileage()

    def test_nobody_ties_an_expense_to_a_day_by_hand(self):
        day = self.finish()
        mine = self.env["hr.expense"].with_user(self.worker).create({
            "name": "Lunch", "employee_id": self.employee.id,
            "product_id": self.env.ref("hr_expense.expense_product_meal").id, "total_amount_currency": 10})
        with self.assertRaises(AccessError):
            mine.write({"bf_route_day_id": day.id})
        with self.assertRaises(AccessError):
            self.env["hr.expense"].with_user(self.worker).create({
                "name": "Forged", "employee_id": self.employee.id, "product_id": self.full.id,
                "quantity": 5, "bf_route_day_id": day.id})
        with self.assertRaises(AccessError):
            day.sudo().expense_ids.with_user(self.manager).write({"bf_route_day_id": False})

    def test_expense_copy_leaves_the_day(self):
        day = self.finish()
        copy = day.sudo().expense_ids.with_user(self.worker).copy()
        self.assertFalse(copy.bf_route_day_id)

    def test_archived_product_claims_nothing(self):
        self.full.active = False
        day = self.finish()
        self.assertFalse(day.sudo().expense_ids)
        with self.assertRaisesRegex(UserError, "No mileage expense category"):
            day.with_user(self.manager).action_claim_mileage()

    # ------------------------------------------------------------------
    # 18.0.1.1.0: the settings and the count of Mileage Tiers
    # ------------------------------------------------------------------
    def test_the_old_settings_are_gone(self):
        for name in ("bf_route_mileage_product_id", "bf_route_mileage_reduced_product_id",
                     "bf_route_mileage_threshold"):
            self.assertNotIn(name, self.env["res.company"]._fields)
        self.assertFalse(self.env["ir.model.fields"].search_count([
            ("model", "=", "res.company"), ("name", "like", "bf\\_route\\_mileage%")]))

    def test_the_expenses_settings_drive_the_day(self):
        # Written through the settings' related field (execute() would also run the other
        # applications' settings, some of which call out).
        settings = self.env["res.config.settings"].with_company(self.company).create({})
        settings.write({"bf_mileage_threshold": 50})
        self.assertEqual(self.company.bf_mileage_threshold, 50)
        day = self.finish()
        lines = {e.product_id: e.quantity for e in day.sudo().expense_ids}
        self.assertEqual((lines[self.full], lines[self.reduced]), (50.0, 35.0))

    def test_another_full_rate_category_is_used(self):
        other = self.full.copy({"name": "Car allowance", "default_code": "CAR1"})
        other.with_company(self.company).standard_price = 0.80
        self.company.bf_mileage_product_id = other
        day = self.finish()
        self.assertEqual(day.sudo().expense_ids.product_id, other)
        self.assertAlmostEqual(day.sudo().expense_ids.total_amount_currency, 68.0)
        self.assertNotIn("beyond", day.sudo().expense_ids.name, "the company's full rate, named so")

    def test_the_company_s_category_without_rate_claims_nothing(self):
        # Odoo's own Mileage still has its rate: the day must judge the company's category.
        other = self.full.copy({"name": "Car allowance", "default_code": "CAR1"})
        other.with_company(self.company).standard_price = 0.0
        self.company.bf_mileage_product_id = other
        day = self.finish()
        self.assertFalse(day.sudo().expense_ids)
        self.assertIn("No mileage expense category with a rate", day.message_ids[0].body)

    def test_one_count_with_the_add_mileage_wizard(self):
        day = self.finish()
        self.assertEqual(day.sudo().expense_ids.product_id, self.full)
        wizard = self.env["bf.expense.mileage.wizard"].with_user(self.worker).create({
            "employee_id": self.employee.id, "company_id": self.company.id,
            "date": MONDAY.replace(day=20), "name": "Depot to the plant", "distance_km": 4950})
        made = self.env["hr.expense"].search(wizard.action_create()["domain"])
        lines = {e.product_id: e.quantity for e in made}
        self.assertEqual((lines[self.full], lines[self.reduced]), (4915.0, 35.0),
                         "the wizard counts the 85 km of the route day")

    def test_a_hand_claim_dated_later_counts_too(self):
        # The year is one count, whatever the date: like the wizard, a day counts it.
        self.manual(4950, when=MONDAY.replace(month=12, day=1))
        day = self.finish()
        lines = {e.product_id: e.quantity for e in day.sudo().expense_ids}
        self.assertEqual((lines[self.full], lines[self.reduced]), (50.0, 35.0))

    def test_a_redone_day_does_not_count_its_own_claim(self):
        self.manual(4990)
        day = self.finish(end=1005)
        self.assertEqual(day.sudo().expense_ids.product_id, self.full)
        day.with_user(self.manager).write({"odometer_end": 1085})
        lines = {e.product_id: e.quantity for e in day.sudo().expense_ids}
        self.assertEqual((lines[self.full], lines[self.reduced]), (10.0, 75.0))

    def test_a_later_day_already_reported_is_not_counted_by_an_earlier_one(self):
        self.manual(4900)
        first = self.finish()
        second = self.finish(start=2000, end=2085, day=self.make_day(day=MONDAY.replace(day=19)))
        sheet = self.env["hr.expense.sheet"].create({
            "name": "November", "employee_id": self.employee.id,
            "expense_line_ids": [(6, 0, second.sudo().expense_ids.ids)]})
        sheet.action_submit_sheet()
        first.with_user(self.manager).write({"odometer_end": 1090})
        self.assertEqual(first.sudo().expense_ids.product_id, self.full,
                         "the later day counted this one: it does not count it back")
        self.assertAlmostEqual(first.sudo().expense_ids.quantity, 90.0)
        self.assertEqual(second.sudo().expense_ids.sheet_id, sheet, "reported: never redone")

    def test_two_days_on_the_same_date_count_in_the_order_they_were_made(self):
        south = self.route.with_user(self.manager).copy({"name": "South shore"})
        self.manual(4900)
        first = self.finish()
        second = self.finish(start=2000, end=2085, day=self.make_day(route=south))
        self.assertEqual(first.date, second.date)
        self.assertLess(first.id, second.id)
        lines = {e.product_id: e.quantity for e in second.sudo().expense_ids}
        self.assertEqual((lines[self.full], lines[self.reduced]), (15.0, 70.0))
        first.with_user(self.manager).write({"odometer_end": 1090})
        self.assertEqual(first.sudo().expense_ids.product_id, self.full,
                         "the day made after it, the same date, counts it: not the other way")
        self.assertAlmostEqual(first.sudo().expense_ids.quantity, 90.0)
        lines = {e.product_id: e.quantity for e in second.sudo().expense_ids}
        self.assertEqual((lines[self.full], lines[self.reduced]), (10.0, 75.0), "and it is redone")

    def test_another_employee_s_claims_are_not_counted(self):
        other_employee = self.env["hr.employee"].create({
            "name": "Other", "user_id": self.other.id, "company_id": self.company.id})
        self.manual(4990)
        self.env["hr.expense"].create({
            "name": "Theirs", "employee_id": other_employee.id, "product_id": self.full.id,
            "quantity": 3000, "date": MONDAY})
        day = self.finish()
        lines = {e.product_id: e.quantity for e in day.sudo().expense_ids}
        self.assertEqual((lines[self.full], lines[self.reduced]), (10.0, 75.0))

    def test_the_routes_settings_show_the_shared_fields(self):
        arch = self.env["res.config.settings"].get_views([(False, "form")])["views"]["form"]["arch"]
        block = arch[arch.index('name="bf_route_mileage"'):]
        block = block[:block.index("</block>")]
        for name in ("bf_mileage_product_id", "bf_mileage_rate", "bf_mileage_reduced_product_id",
                     "bf_mileage_reduced_rate", "bf_mileage_threshold"):
            self.assertIn(f'name="{name}"', block)
        self.assertNotIn("bf_route_mileage_", block.replace('name="bf_route_mileage"', ""))

    def _migrate(self, route, shared, company=None):
        """Run the 18.0.1.1.0 migration on columns put back for the test (rolled back with it)."""
        migration = _migration()
        cr = self.env.cr
        cr.execute("""ALTER TABLE res_company ADD COLUMN bf_route_mileage_product_id int,
                      ADD COLUMN bf_route_mileage_reduced_product_id int,
                      ADD COLUMN bf_route_mileage_threshold int""")
        self.env.flush_all()
        cr.execute("""UPDATE res_company SET bf_route_mileage_product_id = %s,
                      bf_route_mileage_reduced_product_id = %s, bf_route_mileage_threshold = %s,
                      bf_mileage_product_id = %s, bf_mileage_reduced_product_id = %s,
                      bf_mileage_threshold = %s WHERE id = %s""", [*route, *shared, (company or self.company).id])
        migration.migrate(cr, "18.0.1.0.0")
        cr.execute("""SELECT bf_mileage_product_id, bf_mileage_reduced_product_id, bf_mileage_threshold
                      FROM res_company WHERE id = %s""", [(company or self.company).id])
        return cr.fetchone()

    def test_migration_brings_the_routes_settings(self):
        self.make_day()
        other = self.full.copy({"default_code": "CAR1"})
        other_reduced = self.reduced.copy({"default_code": "CAR2"})
        with self.assertLogs("bf_route_expense_migrate_1_1_0", level="WARNING") as logs:
            got = self._migrate((other.id, other_reduced.id, 3000), (self.full.id, self.reduced.id, 5000))
        self.assertEqual(got, (other.id, other_reduced.id, 3000))
        self.assertEqual(len([m for m in logs.output if "which differed" in m]), 3)

    def test_migration_never_erases_a_shared_setting(self):
        self.make_day()
        got = self._migrate((None, None, 5000), (self.full.id, self.reduced.id, 5000))
        self.assertEqual(got, (self.full.id, self.reduced.id, 5000))

    def test_migration_leaves_a_company_without_route_days_alone(self):
        bare = self.env["res.company"].create({"name": "No routes", "currency_id": self.company.currency_id.id})
        self.assertFalse(self.env["bf.route.day"].search_count([("company_id", "=", bare.id)]))
        other = self.full.copy({"default_code": "CAR1"})
        with self.assertLogs("bf_route_expense_migrate_1_1_0", level="WARNING") as logs:
            got = self._migrate((other.id, self.reduced.id, 5000), (self.full.id, self.reduced.id, 0), company=bare)
        self.assertEqual(got, (self.full.id, self.reduced.id, 0), "its Expenses settings stay")
        self.assertTrue(any("has no route day" in m and "dropped" in m for m in logs.output),
                        "and the log says what the routes' side held")

    def test_migration_of_a_new_install_does_nothing(self):
        # Columns put back and differing: a new install (no version) must still not touch them.
        self.make_day()
        other = self.full.copy({"default_code": "CAR1"})
        self.env.cr.execute("""ALTER TABLE res_company ADD COLUMN bf_route_mileage_product_id int,
                               ADD COLUMN bf_route_mileage_reduced_product_id int,
                               ADD COLUMN bf_route_mileage_threshold int""")
        self.env.flush_all()
        self.env.cr.execute("UPDATE res_company SET bf_route_mileage_product_id = %s, "
                            "bf_route_mileage_threshold = 3000 WHERE id = %s", [other.id, self.company.id])
        migration = _migration()
        migration.migrate(self.env.cr, None)
        self.env.cr.execute("SELECT bf_mileage_threshold FROM res_company WHERE id = %s", [self.company.id])
        self.assertEqual(self.env.cr.fetchone()[0], 5000)
