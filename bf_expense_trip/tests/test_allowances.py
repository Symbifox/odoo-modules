# -*- coding: utf-8 -*-
from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import TripCommon


@tagged("post_install", "-at_install")
class TestAllowances(TripCommon):

    def _allowances(self, trip):
        return {e.name: (round(e.total_amount_currency, 2), e.tax_ids, e.date)
                for e in trip.expense_ids.filtered("bf_trip_allowance")}

    def test_ticked_boxes_at_the_rates(self):
        self._rates()
        trip = self._trip()
        first, second, third = trip.day_ids.sorted("date")
        second.lunch = False          # a conference lunch
        third.dinner = False          # home for dinner
        first.private_lodging = True  # a night at a friend's
        trip.action_generate_allowances()
        made = trip.expense_ids.filtered("bf_trip_allowance")
        totals = {e.description: e.total_amount_currency for e in made}
        self.assertEqual(round(sum(made.mapped("total_amount_currency")), 2),
                         3 * 10 + 2 * 20 + 2 * 30 + 3 * 5 + 50)
        self.assertEqual(len(made), 5, totals)
        self.assertFalse(made.tax_ids)
        self.assertEqual(made.product_id, self.allowance)
        self.assertEqual(made.employee_id, self.boss)
        self.assertEqual(first.amount, 10 + 20 + 30 + 5 + 50)
        self.assertEqual(second.amount, 10 + 30 + 5)

    def test_a_rate_that_changes_during_the_trip(self):
        self._rates()
        self.Rate.create({"company_id": self.company.id, "kind": "breakfast", "amount": 12.0,
                          "date_from": date(2026, 11, 3), "product_id": self.allowance.id})
        trip = self._trip()
        trip.day_ids.write({"lunch": False, "dinner": False, "incidentals": False})
        trip.action_generate_allowances()
        made = trip.expense_ids.filtered("bf_trip_allowance").sorted("date")
        self.assertEqual([(m.date, m.total_amount_currency) for m in made],
                         [(date(2026, 11, 2), 10.0), (date(2026, 11, 3), 24.0)])

    def test_generating_again_replaces(self):
        self._rates()
        trip = self._trip()
        trip.action_generate_allowances()
        before = trip.expense_ids.filtered("bf_trip_allowance")
        trip.day_ids.write({"breakfast": False})
        trip.action_generate_allowances()
        after = trip.expense_ids.filtered("bf_trip_allowance")
        self.assertFalse(before.exists())
        self.assertEqual(round(sum(after.mapped("total_amount_currency")), 2), 3 * (20 + 30 + 5))

    def test_reported_allowances_are_not_redone(self):
        self._rates()
        trip = self._trip()
        trip.action_generate_allowances()
        trip.action_create_report()
        with self.assertRaisesRegex(UserError, "already in a report"):
            trip.action_generate_allowances()

    def test_a_missing_rate_says_which(self):
        self._rates()
        self.Rate.search([("kind", "=", "incidentals")]).unlink()
        trip = self._trip()
        with self.assertRaisesRegex(UserError, "Incidentals"):
            trip.action_generate_allowances()
        self.assertFalse(trip.expense_ids)

    def test_a_rate_before_its_start_date_does_not_apply(self):
        self.Rate.create({"company_id": self.company.id, "kind": "breakfast", "amount": 10.0,
                          "date_from": date(2026, 11, 3), "product_id": self.allowance.id})
        trip = self._trip()
        trip.day_ids.write({"lunch": False, "dinner": False, "incidentals": False})
        trip.day_ids.sorted("date")[0].breakfast = False
        trip.action_generate_allowances()
        self.assertEqual(sum(trip.expense_ids.mapped("total_amount_currency")), 20.0)
        trip.day_ids.sorted("date")[0].breakfast = True
        with self.assertRaisesRegex(UserError, "Breakfast"):
            trip.action_generate_allowances()

    def test_a_category_with_a_cost_is_refused(self):
        mileage = self.env.ref("hr_expense.expense_product_mileage")
        mileage.with_company(self.company).standard_price = 0.73
        with self.assertRaises(ValidationError):
            self.Rate.create({"company_id": self.company.id, "kind": "lunch", "amount": 20.0,
                              "date_from": date(2026, 1, 1), "product_id": mileage.id})

    def test_federal_rates_only_in_canadian_dollars(self):
        if self.company.currency_id == self.env.ref("base.CAD"):
            self.skipTest("the test company keeps its books in CAD")
        with self.assertRaisesRegex(UserError, "Canadian dollars"):
            self.Rate.with_company(self.company).action_load_njc_rates()
        self.assertFalse(self.Rate.search([("company_id", "=", self.company.id)]))

    def test_federal_rates_load_once(self):
        cad_company = self.env["res.company"].create({
            "name": "Boréal (essai)", "currency_id": self.env.ref("base.CAD").id})
        self.env.user.company_ids |= cad_company
        Rate = self.Rate.with_company(cad_company)
        Rate.action_load_njc_rates()
        Rate.action_load_njc_rates()
        rates = Rate.search([("company_id", "=", cad_company.id)])
        self.assertEqual(sorted(rates.mapped("amount")), [25.0, 29.95, 30.55, 50.0, 62.7])
        self.assertEqual(set(rates.mapped("date_from")), {date(2026, 10, 1)})

    def test_an_allowance_claims_no_tax_whatever_its_category(self):
        """⚠️ The category the office chooses may carry taxes (a « Meals » category does): an
        allowance has no receipt, so no tax was paid that could be claimed back."""
        taxed = self.env["product.product"].create({
            "name": "Repas (catégorie taxée)", "type": "service", "can_be_expensed": True,
            "standard_price": 0.0, "supplier_taxes_id": [(6, 0, self.taxe.ids)]})
        self.Rate.create({"company_id": self.company.id, "kind": "dinner", "amount": 30.0,
                          "date_from": date(2026, 1, 1), "product_id": taxed.id})
        trip = self._trip()
        trip.day_ids.write({"breakfast": False, "lunch": False, "incidentals": False})
        trip.action_generate_allowances()
        made = trip.expense_ids.filtered("bf_trip_allowance")
        self.assertEqual(made.product_id, taxed)
        self.assertFalse(made.tax_ids)
        self.assertEqual(made.tax_amount_currency, 0.0)
        self.assertEqual(made.total_amount_currency, 90.0)
