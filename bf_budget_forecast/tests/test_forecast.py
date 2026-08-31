from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import new_test_user, tagged

from odoo.addons.bf_budget.tests.common import BfBudgetCommon


@tagged("post_install", "-at_install")
class TestForecast(BfBudgetCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        today = fields.Date.context_today(cls.env.user)
        # Un horizon de 18 mois qui TRAVERSE l'exercice, et un réel arrêté
        # à la fin du mois dernier.
        cls.h_start = (today - relativedelta(months=6)).replace(day=1)
        cls.h_end = cls.h_start + relativedelta(months=18, days=-1)
        cls.through = (today.replace(day=1) - relativedelta(days=1))

    def _forecast(self, **extra):
        vals = {
            "name": "Exploitation glissante",
            "company_id": self.company.id,
            "date_start": self.h_start,
            "date_end": self.h_end,
            "actuals_through": self.through,
        }
        vals.update(extra)
        f = self.env["bf.budget.forecast"].create(vals)
        self.env["bf.budget.forecast.line"].create(
            {"forecast_id": f.id, "position_id": self.position_software.id}
        )
        return f

    # ------------------------------------------------------------------
    def test_the_horizon_crosses_the_exercise(self):
        """🔴 Rien ne doit supposer douze mois ni un début au 1er janvier."""
        f = self._forecast()
        periods = f.line_ids.period_ids.sorted("date_start")
        self.assertEqual(len(periods), 18)
        self.assertEqual(periods[0].date_start, self.h_start)
        self.assertEqual(periods[-1].date_end, self.h_end)
        self.assertNotEqual(periods[0].date_start.year, periods[-1].date_start.year)

    def test_closed_months_read_the_books_open_months_read_the_forecast(self):
        f = self._forecast()
        line = f.line_ids
        clos = line.period_ids.filtered("is_closed").sorted("date_start")
        ouverts = (line.period_ids - clos).sorted("date_start")
        self.assertTrue(clos and ouverts)
        self._post_bill(self.account_software, 640.0, date=clos[-1].date_start)
        ouverts[0].amount_forecast = 900.0
        line.invalidate_recordset()
        self.assertEqual(clos[-1].amount, 640.0)
        self.assertEqual(ouverts[0].amount, 900.0)
        self.assertEqual(line.amount_actual, 640.0)
        self.assertEqual(line.amount_forecast, 900.0)
        self.assertEqual(line.amount_total, 1540.0)

    def test_a_closed_month_is_not_forecast_again(self):
        f = self._forecast()
        clos = f.line_ids.period_ids.filtered("is_closed")[0]
        with self.assertRaises(UserError):
            clos.amount_forecast = 500.0

    def test_the_forecast_of_a_closed_month_survives_it(self):
        """🔴 C'est ce qui rend la prévision glissante utile.

        « On avait prévu 900, il en est venu 1 240 » n'est calculable que si le
        chiffre prévu n'a pas été écrasé au moment où le mois s'est clos.
        """
        f = self._forecast()
        line = f.line_ids
        futur = (line.period_ids - line.period_ids.filtered("is_closed")).sorted("date_start")[0]
        futur.amount_forecast = 900.0
        self._post_bill(self.account_software, 1240.0, date=futur.date_start)
        # Le mois se clôt : on avance la date d'arrêt du réel.
        f.actuals_through = futur.date_end
        f.invalidate_recordset()
        futur.invalidate_recordset()
        self.assertTrue(futur.is_closed)
        self.assertEqual(futur.amount_forecast, 900.0)
        self.assertEqual(futur.amount_actual, 1240.0)
        self.assertEqual(futur.amount, 1240.0)
        self.assertEqual(futur.variance, 340.0)

    def test_publishing_freezes_the_vintage(self):
        f = self._forecast()
        f.action_publish()
        self.assertEqual(f.state, "published")
        with self.assertRaises(UserError):
            f.write({"date_end": self.h_end + relativedelta(months=1)})
        ouvert = (f.line_ids.period_ids - f.line_ids.period_ids.filtered("is_closed"))[0]
        with self.assertRaises(UserError):
            ouvert.amount_forecast = 42.0

    def test_rolling_forward_shifts_and_carries(self):
        f = self._forecast()
        line = f.line_ids
        ouverts = (line.period_ids - line.period_ids.filtered("is_closed")).sorted("date_start")
        ouverts[0].amount_forecast = 700.0
        ouverts[1].amount_forecast = 800.0
        action = f.action_roll_forward()
        suivante = self.env["bf.budget.forecast"].browse(action["res_id"])
        self.assertEqual(f.state, "superseded")
        self.assertEqual(suivante.vintage, 2)
        self.assertEqual(suivante.previous_id, f)
        self.assertEqual(suivante.date_start, self.h_start + relativedelta(months=1))
        self.assertEqual(suivante.date_end, self.h_end + relativedelta(months=1))
        self.assertEqual(len(suivante.line_ids.period_ids), 18)
        # Les mois qui existaient gardent EXACTEMENT leur chiffre.
        reportes = {p.date_start: p.amount_forecast for p in suivante.line_ids.period_ids}
        self.assertEqual(reportes[ouverts[0].date_start], 700.0)
        self.assertEqual(reportes[ouverts[1].date_start], 800.0)

    def test_rolling_twice_keeps_the_chain(self):
        f = self._forecast()
        a = self.env["bf.budget.forecast"].browse(f.action_roll_forward()["res_id"])
        b = self.env["bf.budget.forecast"].browse(a.action_roll_forward()["res_id"])
        self.assertEqual(b.vintage, 3)
        self.assertEqual(b.previous_id, a)
        self.assertEqual(a.previous_id, f)
        self.assertEqual(a.state, "superseded")

    def test_the_seed_uses_the_closed_months(self):
        """Sans amorce, la passe mensuelle se ressaisit à la main et meurt."""
        f = self._forecast()
        line = f.line_ids
        clos = line.period_ids.filtered("is_closed").sorted("date_start")
        for period in clos:
            self._post_bill(self.account_software, 300.0, date=period.date_start)
        line.invalidate_recordset()
        line._seed_open_months()
        ouverts = line.period_ids - line.period_ids.filtered("is_closed")
        self.assertTrue(all(p.amount_forecast == 300.0 for p in ouverts))

    def test_the_seed_says_nothing_when_it_knows_nothing(self):
        f = self._forecast(actuals_through=self.h_start - relativedelta(days=1))
        f.line_ids._seed_open_months()
        self.assertTrue(all(p.amount_forecast == 0.0 for p in f.line_ids.period_ids))

    def test_comparing_two_vintages(self):
        """La seule chose qu'une prévision glissante apporte vraiment."""
        f = self._forecast()
        ouverts = (f.line_ids.period_ids - f.line_ids.period_ids.filtered("is_closed")).sorted("date_start")
        ouverts[0].amount_forecast = 500.0
        suivante = self.env["bf.budget.forecast"].browse(f.action_roll_forward()["res_id"])
        cible = suivante.line_ids.period_ids.filtered(
            lambda p: p.date_start == ouverts[1].date_start
        )
        cible.amount_forecast = 2000.0
        suivante.invalidate_recordset()
        rows = suivante.compare_to(f)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["position"], self.position_software)
        self.assertGreater(rows[0]["delta"], 0.0)

    def test_the_actuals_line_cannot_be_in_the_future(self):
        today = fields.Date.context_today(self.env.user)
        with self.assertRaises(ValidationError):
            self._forecast(actuals_through=today + relativedelta(months=1))

    def test_an_horizon_fully_closed_is_not_a_forecast(self):
        with self.assertRaises(ValidationError):
            self._forecast(actuals_through=self.h_end)

    def test_a_published_vintage_is_not_deleted(self):
        f = self._forecast()
        f.action_publish()
        with self.assertRaises(UserError):
            f.unlink()

    def test_a_rolled_vintage_does_not_reopen(self):
        f = self._forecast()
        f.action_roll_forward()
        with self.assertRaises(UserError):
            f.action_reset_draft()

    def test_one_line_per_position(self):
        f = self._forecast()
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self.env["bf.budget.forecast.line"].create(
                    {"forecast_id": f.id, "position_id": self.position_software.id}
                )

    def test_roles_load_the_views(self):
        reader = new_test_user(
            self.env, login="prev_lecture",
            groups="base.group_user,bf_budget.group_bf_budget_user",
            company_id=self.company.id, company_ids=[(6, 0, [self.company.id])],
        )
        manager = new_test_user(
            self.env, login="prev_gestion",
            groups="base.group_user,bf_budget.group_bf_budget_manager",
            company_id=self.company.id, company_ids=[(6, 0, [self.company.id])],
        )
        for account in (reader, manager):
            for model, view in (
                ("bf.budget.forecast", "list"), ("bf.budget.forecast", "form"),
                ("bf.budget.forecast.line", "list"),
                ("bf.budget.forecast.period", "list"),
            ):
                self.env[model].with_user(account).get_view(view_type=view)

    def test_a_reader_does_not_write_a_forecast(self):
        from odoo.exceptions import AccessError
        reader = new_test_user(
            self.env, login="prev_lecture2",
            groups="base.group_user,bf_budget.group_bf_budget_user",
            company_id=self.company.id, company_ids=[(6, 0, [self.company.id])],
        )
        f = self._forecast()
        with self.assertRaises(AccessError):
            f.with_user(reader).write({"name": "interdit"})

    def test_carrying_is_allowed_where_forecasting_is_not(self):
        """🔴 La garde distingue deux gestes qui écrivent le même champ.

        Un utilisateur ne re-prévoit pas un mois clos. Le report d'une passe à la
        suivante, lui, doit pouvoir recopier la prévision historique dans un mois
        entre-temps clos : sans cette mémoire, la comparaison entre millésimes
        s'efface dès la deuxième passe.
        """
        f = self._forecast()
        line = f.line_ids
        ouverts = (line.period_ids - line.period_ids.filtered("is_closed")).sorted("date_start")
        ouverts[0].amount_forecast = 1500.0
        cible_date = ouverts[0].date_start
        suivante = self.env["bf.budget.forecast"].browse(f.action_roll_forward()["res_id"])
        # Dans la passe suivante, ce mois est clos, et il a gardé la prévision.
        report = suivante.line_ids.period_ids.filtered(lambda p: p.date_start == cible_date)
        self.assertTrue(report.is_closed)
        self.assertEqual(report.amount_forecast, 1500.0)
        # Mais personne ne peut le re-prévoir à la main.
        suivante.action_reset_draft() if suivante.state == "published" else None
        with self.assertRaises(UserError):
            report.amount_forecast = 99.0
