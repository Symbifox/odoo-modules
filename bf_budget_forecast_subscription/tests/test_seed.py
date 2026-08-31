from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import tagged

from odoo.addons.bf_budget_subscription.tests.common import BfBudgetSubscriptionCommon


@tagged("post_install", "-at_install")
class TestSeedFromCalendar(BfBudgetSubscriptionCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        today = fields.Date.context_today(cls.env.user)
        cls.h_start = (today - relativedelta(months=6)).replace(day=1)
        cls.h_end = cls.h_start + relativedelta(months=18, days=-1)
        cls.through = today.replace(day=1) - relativedelta(days=1)

    def _forecast_line(self):
        f = self.env["bf.budget.forecast"].create(
            {
                "name": "Glissante",
                "company_id": self.company.id,
                "date_start": self.h_start,
                "date_end": self.h_end,
                "actuals_through": self.through,
            }
        )
        return f, self.env["bf.budget.forecast.line"].create(
            {"forecast_id": f.id, "position_id": self.position_software.id}
        )

    def test_without_a_subscription_the_socle_average_still_applies(self):
        f, line = self._forecast_line()
        clos = line.period_ids.filtered("is_closed").sorted("date_start")
        for period in clos:
            self._post_bill(self.account_software, 300.0, date=period.date_start)
        line.invalidate_recordset()
        line._seed_open_months()
        ouverts = line.period_ids - line.period_ids.filtered("is_closed")
        self.assertTrue(all(p.amount_forecast == 300.0 for p in ouverts))

    def test_a_yearly_renewal_lands_on_its_month_not_spread(self):
        """🔴 Ce que la moyenne plate rate.

        Une échéance annuelle de 1 200 $ étalée sur douze mois met 100 $ partout
        et sous-estime de 1 100 $ le mois où elle tombe. Le calendrier la remet
        où elle est.
        """
        sub = self._make_subscription(
            "annual", 1200.0, start=self.h_start, name="Licence annuelle"
        )
        sub.budget_position_id = self.position_software
        f, line = self._forecast_line()
        line.invalidate_recordset()
        line._seed_open_months()
        par_mois = {p.name: p.amount_forecast for p in line.period_ids if not p.is_closed}
        anniversaire = (self.h_start + relativedelta(months=12)).strftime("%Y-%m")
        self.assertIn(anniversaire, par_mois)
        self.assertGreaterEqual(par_mois[anniversaire], 1200.0)
        autres = [v for k, v in par_mois.items() if k != anniversaire]
        self.assertTrue(all(v < 1200.0 for v in autres))

    def test_the_recurring_part_is_not_counted_twice(self):
        """🔴 Le cœur du pont.

        Le réel des mois clos contient déjà les échéances de ces mois-là.
        Moyenner ce réel brut PUIS rajouter le calendrier gonflerait la prévision
        de tout le poids du récurrent — la partie du budget qu'on connaît le
        mieux serait celle qu'on estimerait le plus mal.
        """
        sub = self._make_subscription("monthly", 100.0, start=self.h_start)
        sub.budget_position_id = self.position_software
        f, line = self._forecast_line()
        clos = line.period_ids.filtered("is_closed").sorted("date_start")
        # Chaque mois clos : 100 $ d'abonnement, plus 50 $ d'autre chose.
        for period in clos:
            self._post_bill(self.account_software, 150.0, date=period.date_start)
        line.invalidate_recordset()
        line._seed_open_months()
        ouverts = (line.period_ids - line.period_ids.filtered("is_closed")).sorted("date_start")
        # Attendu : 100 (daté) + 50 (le reste moyenné) = 150. Le défaut aurait
        # donné 150 (moyenne brute) + 100 (daté) = 250.
        self.assertAlmostEqual(ouverts[0].amount_forecast, 150.0, places=2)

    def test_an_on_demand_subscription_falls_into_the_average(self):
        """Sans calendrier, mieux vaut l'étaler que le perdre."""
        sub = self._make_subscription("on_demand", 80.0, start=self.h_start)
        sub.budget_position_id = self.position_software
        f, line = self._forecast_line()
        clos = line.period_ids.filtered("is_closed").sorted("date_start")
        for period in clos:
            self._post_bill(self.account_software, 80.0, date=period.date_start)
        line.invalidate_recordset()
        line._seed_open_months()
        ouverts = line.period_ids - line.period_ids.filtered("is_closed")
        self.assertTrue(all(abs(p.amount_forecast - 80.0) < 0.01 for p in ouverts))

    def test_no_history_falls_back_to_the_calendar_alone(self):
        sub = self._make_subscription("monthly", 100.0, start=self.h_start)
        sub.budget_position_id = self.position_software
        f = self.env["bf.budget.forecast"].create(
            {
                "name": "Sans historique",
                "company_id": self.company.id,
                "date_start": self.h_start,
                "date_end": self.h_end,
                "actuals_through": self.h_start - relativedelta(days=1),
            }
        )
        line = self.env["bf.budget.forecast.line"].create(
            {"forecast_id": f.id, "position_id": self.position_software.id}
        )
        line._seed_open_months()
        ouverts = line.period_ids.sorted("date_start")
        self.assertAlmostEqual(ouverts[0].amount_forecast, 100.0, places=2)
