from dateutil.relativedelta import relativedelta

from odoo.tests.common import tagged

from .common import ExCase


@tagged("post_install", "-at_install")
class TestIndicator(ExCase):

    def test_headcount_hires_and_turnover(self):
        period = (self.today - relativedelta(months=1)).replace(day=1)
        month_end = period + relativedelta(months=1) - relativedelta(days=1)

        # Deux personnes déjà là avant le mois, une seule y reste.
        old_a = self._employee("Ancienne A", months=24)
        old_b = self._employee("Ancienne B", months=24)
        old_b.departure_date = month_end
        # Une arrivée pendant le mois.
        self._employee("Arrivée", months=None)
        newcomer = self.env["hr.employee"].search([("name", "=", "Arrivée")], limit=1)
        self.env["hr.contract"].create({
            "name": "Contrat arrivée", "employee_id": newcomer.id,
            "date_start": period + relativedelta(days=5),
            "wage": 4000.0, "state": "open", "company_id": self.company.id,
        })
        newcomer.invalidate_recordset(["first_contract_date"])

        record = self.env["bf.ex.indicator"]._compute_period(self.company, period)

        self.assertEqual(record.period, period)
        self.assertGreaterEqual(record.headcount_start, 2)
        self.assertEqual(record.hires, 1)
        self.assertEqual(record.departures, 1)
        self.assertGreater(record.turnover_rate, 0.0)
        self.assertTrue(old_a.exists())

    def test_median_tenure(self):
        period = (self.today - relativedelta(months=1)).replace(day=1)
        for months in (6, 12, 36):
            self._employee("Ancienneté %s" % months, months=months)
        record = self.env["bf.ex.indicator"]._compute_period(self.company, period)
        self.assertGreater(record.median_tenure_months, 0.0)

    def test_recompute_is_idempotent(self):
        period = (self.today - relativedelta(months=1)).replace(day=1)
        first = self.env["bf.ex.indicator"]._compute_period(self.company, period)
        second = self.env["bf.ex.indicator"]._compute_period(self.company, period)
        self.assertEqual(first, second, "un seul relevé par mois et par société")

    def test_period_is_snapped_to_first_of_month(self):
        mid = (self.today - relativedelta(months=1)).replace(day=17)
        record = self.env["bf.ex.indicator"]._compute_period(self.company, mid)
        self.assertEqual(record.period.day, 1)

    def test_benefit_cost_of_the_month(self):
        period = (self.today - relativedelta(months=1)).replace(day=1)
        benefit = self._benefit("Repas", cost_model="per_use", cost_amount=0.0)
        emp = self._employee("Mangeuse", months=12)
        line = self.env["bf.ex.usage"].create({
            "employee_id": emp.id, "benefit_id": benefit.id,
            "date": period + relativedelta(days=3), "amount": 42.0,
        })
        line.action_confirm()
        # Une ligne hors du mois ne doit pas compter.
        outside = self.env["bf.ex.usage"].create({
            "employee_id": emp.id, "benefit_id": benefit.id,
            "date": self.today, "amount": 999.0,
        })
        outside.action_confirm()

        record = self.env["bf.ex.indicator"]._compute_period(self.company, period)
        self.assertAlmostEqual(record.benefit_cost, 42.0)

    def test_median_helper(self):
        from odoo.addons.bf_employee_experience.models.indicator import _median
        self.assertEqual(_median([]), 0.0)
        self.assertEqual(_median([5]), 5.0)
        self.assertEqual(_median([1, 3]), 2.0)
        self.assertEqual(_median([3, 1, 2]), 2.0)
