# -*- coding: utf-8 -*-
from datetime import date

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.hr_expense.tests.common import TestExpenseCommon


@tagged("post_install", "-at_install")
class TestMileageTiers(TestExpenseCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.full = cls.env.ref("hr_expense.expense_product_mileage")
        cls.reduced = cls.env.ref("bf_expense_mileage.product_mileage_reduced")
        cls.full.with_company(cls.company).standard_price = 0.73
        cls.reduced.with_company(cls.company).standard_price = 0.67
        cls.company.write({
            "bf_mileage_product_id": cls.full.id,
            "bf_mileage_reduced_product_id": cls.reduced.id,
            "bf_mileage_threshold": 5000,
        })
        cls.day = date(2026, 6, 15)

    def _mileage(self, km, day=None, employee=None, product=None, **vals):
        return self.env["hr.expense"].create({
            "name": "Earlier mileage",
            "employee_id": (employee or self.expense_employee).id,
            "product_id": (product or self.full).id,
            "quantity": km,
            "date": day or date(2026, 3, 1),
            "company_id": self.company.id,
            **vals,
        })

    def _claim(self, km, day=None, user=None, employee=None):
        Wizard = self.env["bf.expense.mileage.wizard"]
        if user:
            Wizard = Wizard.with_user(user)
        wizard = Wizard.create({
            "employee_id": (employee or self.expense_employee).id,
            "company_id": self.company.id,
            "date": day or self.day,
            "name": "Office to the plant and back",
            "distance_km": km,
        })
        action = wizard.action_create()
        return self.env["hr.expense"].search(action["domain"], order="id")

    def _split(self, expenses):
        return [(e.product_id, round(e.quantity, 1), round(e.total_amount_currency, 2))
                for e in expenses]

    def test_crossing_the_threshold_gives_two_expenses(self):
        self._mileage(4900)
        made = self._claim(250)
        self.assertEqual(self._split(made), [(self.full, 100.0, 73.0), (self.reduced, 150.0, 100.5)])

    def test_under_the_threshold_one_expense_at_the_full_rate(self):
        made = self._claim(120)
        self.assertEqual(self._split(made), [(self.full, 120.0, 87.6)])

    def test_beyond_the_threshold_one_expense_at_the_reduced_rate(self):
        self._mileage(3000)
        self._mileage(2500, product=self.reduced)
        made = self._claim(40)
        self.assertEqual(self._split(made), [(self.reduced, 40.0, 26.8)])

    def test_threshold_zero_keeps_the_full_rate(self):
        self.company.bf_mileage_threshold = 0
        self._mileage(9000)
        made = self._claim(10)
        self.assertEqual(self._split(made), [(self.full, 10.0, 7.3)])

    def test_what_is_not_counted(self):
        """Another year, another employee, a refusal, a negative draft."""
        other = self.env["hr.employee"].create({"name": "Someone else"})
        self._mileage(4999, day=date(2025, 12, 31))
        self._mileage(4999, day=date(2027, 1, 1))
        self._mileage(4999, employee=other)
        refused = self._mileage(4999)
        self._mileage(-200)
        sheet = self.env["hr.expense.sheet"].create({
            "name": "Refused", "employee_id": self.expense_employee.id,
            "expense_line_ids": [(6, 0, refused.ids)]})
        sheet.action_submit_sheet()
        sheet._do_refuse("Not a business trip")
        self.assertEqual(refused.state, "refused")
        made = self._claim(100)
        self.assertEqual(self._split(made), [(self.full, 100.0, 73.0)])

    def test_same_day_counts(self):
        self._mileage(4990, day=self.day)
        made = self._claim(20)
        self.assertEqual(self._split(made), [(self.full, 10.0, 7.3), (self.reduced, 10.0, 6.7)])

    def test_no_rate_refuses(self):
        self.reduced.with_company(self.company).standard_price = 0.0
        self._mileage(4950)
        with self.assertRaisesRegex(UserError, "no rate|pas de taux"):
            self._claim(100)

    def test_no_kilometres_refuses(self):
        with self.assertRaises(UserError):
            self._claim(0)

    def test_an_employee_claims_for_themself_only(self):
        made = self._claim(30, user=self.expense_user_employee)
        self.assertEqual(made.employee_id, self.expense_employee)
        other = self.env["hr.employee"].create({"name": "Someone else"})
        with self.assertRaises(AccessError):
            self._claim(30, user=self.expense_user_employee, employee=other)

    def test_a_submitted_expense_counts(self):
        """An earlier expense of the year already submitted still counts. (The count is read in
        sudo so it never depends on what the claimant can read; with Odoo's rules the owner and
        the assistant read every expense of the year, so this test cannot tell sudo apart.)"""
        earlier = self._mileage(4950)
        sheet = self.env["hr.expense.sheet"].create({
            "name": "Spring", "employee_id": self.expense_employee.id,
            "expense_line_ids": [(6, 0, earlier.ids)]})
        sheet.action_submit_sheet()
        made = self._claim(100, user=self.expense_user_employee)
        self.assertEqual(self._split(made), [(self.full, 50.0, 36.5), (self.reduced, 50.0, 33.5)])

    def test_a_negative_draft_does_not_reopen_the_full_rate(self):
        """A correction typed as a negative quantity would give back kilometres at the full rate."""
        self._mileage(4950)
        self._mileage(-200)
        made = self._claim(100)
        self.assertEqual(self._split(made), [(self.full, 50.0, 36.5), (self.reduced, 50.0, 33.5)])

    def test_a_backdated_claim_does_not_start_the_year_over(self):
        """June already took the 5,000 km at the full rate; a March claim typed afterwards
        goes at the reduced rate (the cap is on the year's total)."""
        self._mileage(5000, day=date(2026, 6, 30))
        made = self._claim(100, day=date(2026, 3, 10))
        self.assertEqual(self._split(made), [(self.reduced, 100.0, 67.0)])

    def test_route_categories_count_too(self):
        """A reduced category of Work routes' mileage (installed after this module, say) is
        part of the same year."""
        other_reduced = self.reduced.copy({"default_code": "MIL2-ROUTES"})
        # Where Work routes' mileage is installed, its record exists (and since 18.0.1.1.0 it
        # depends on this module): point it at the other category instead.
        xmlid = self.env["ir.model.data"].search([
            ("module", "=", "bf_route_expense"), ("name", "=", "product_mileage_reduced")])
        if xmlid:
            xmlid.res_id = other_reduced.id
            # The lookup reads the table in SQL: write it there before forgetting the cache.
            self.env.flush_all()
            self.env.registry.clear_cache()
        else:
            self.env["ir.model.data"].create({
                "module": "bf_route_expense", "name": "product_mileage_reduced",
                "model": "product.product", "res_id": other_reduced.id})
        self._mileage(4950, product=other_reduced)
        made = self._claim(100)
        self.assertEqual(self._split(made), [(self.full, 50.0, 36.5), (self.reduced, 50.0, 33.5)])

    def test_own_reduced_category_counts_when_the_company_chose_another(self):
        """Kilometres claimed with this module's reduced category stay in the year after the
        company points its settings at another one (Work routes' migration may do that)."""
        self._mileage(4950, product=self.reduced)
        # Where Work routes' mileage shares the record, its xmlid would count it anyway: move it.
        routes = self.env["ir.model.data"].search([
            ("module", "=", "bf_route_expense"), ("name", "=", "product_mileage_reduced")])
        if routes:
            routes.res_id = self.reduced.copy({"default_code": "MIL2-ROUTES"}).id
            self.env.flush_all()
            self.env.registry.clear_cache()
        other = self.reduced.copy({"default_code": "MIL2-OTHER"})
        other.with_company(self.company).standard_price = 0.67
        self.company.bf_mileage_reduced_product_id = other
        made = self._claim(100)
        self.assertEqual(self._split(made), [(self.full, 50.0, 36.5), (other, 50.0, 33.5)])

    def test_no_preview_nor_claim_for_someone_one_cannot_choose(self):
        other = self.env["hr.employee"].create({"name": "Someone else"})
        self._mileage(4000, employee=other)
        wizard = self.env["bf.expense.mileage.wizard"].with_user(self.expense_user_employee).create({
            "employee_id": other.id, "company_id": self.company.id, "date": self.day,
            "name": "Probe", "distance_km": 99999})
        self.assertFalse(wizard.preview)
        with self.assertRaises(AccessError):
            wizard.action_create()

    def test_an_approved_negative_correction_counts(self):
        self._mileage(5000)
        correction = self._mileage(-300)
        sheet = self.env["hr.expense.sheet"].create({
            "name": "Correction", "employee_id": self.expense_employee.id,
            "expense_line_ids": [(6, 0, correction.ids)]})
        sheet.action_submit_sheet()
        sheet._do_approve()
        made = self._claim(100)
        self.assertEqual(self._split(made), [(self.full, 100.0, 73.0)])

    def test_odoo_mileage_counts_when_the_company_chose_another_category(self):
        other_full = self.full.copy({"default_code": "MIL-AUTRE"})
        other_full.with_company(self.company).standard_price = 0.73
        self._mileage(4950)  # Odoo's own Mileage, typed by hand
        self.company.bf_mileage_product_id = other_full
        made = self._claim(100)
        self.assertEqual(self._split(made), [(other_full, 50.0, 36.5), (self.reduced, 50.0, 33.5)])

    def test_no_claim_reveals_someone_elses_count(self):
        """Before reading any rate: with the reduced rate at 0, a « no rate » error would come
        only once the threshold is passed, and bisecting the kilometres would read the count."""
        other = self.env["hr.employee"].create({"name": "Someone else"})
        self.reduced.with_company(self.company).standard_price = 0.0
        wizard = self.env["bf.expense.mileage.wizard"].with_user(self.expense_user_employee).create({
            "employee_id": other.id, "company_id": self.company.id, "date": self.day,
            "name": "Probe", "distance_km": 99999})
        with self.assertRaises(AccessError):
            wizard.action_create()
