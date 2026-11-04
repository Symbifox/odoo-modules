from dateutil.relativedelta import relativedelta

from odoo.tests import tagged

from .common import BfBudgetPurchaseCommon


@tagged("post_install", "-at_install")
class TestPurchaseTheoretical(BfBudgetPurchaseCommon):
    """Le théorique de calendrier, avec des fenêtres posées AUTOUR d'aujourd'hui.

    Les dates sont relatives au jour du test (99 jours avant, 100 après) pour
    que « dû » et « à venir » existent tous les deux quelle que soit la date
    d'exécution — un exercice civil n'a plus d'à-venir le 31 décembre. La
    ventilation est posée EXPLICITEMENT (tout au premier mois, entièrement
    passé, ou tout au dernier, entièrement à venir) : le reliquat attendu se
    lit alors sans refaire la formule du module.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.window_start = cls.today - relativedelta(days=99)
        cls.window_end = cls.today + relativedelta(days=100)

    def _window_line(self, planned=1200.0):
        budget = self.env["bf.budget"].create(
            {
                "name": "Fenêtre autour d'aujourd'hui",
                "company_id": self.company.id,
                "date_start": self.window_start,
                "date_end": self.window_end,
            }
        )
        return self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "position_id": self.position_software.id,
                "amount_planned": planned,
            }
        )

    def _ventilate_all(self, line, period_index, total=1200.0):
        """Tout le plan sur UN mois : le premier (entièrement passé, le
        prorata de ventilation vaut alors tout le plan) ou le dernier
        (entièrement à venir, il vaut zéro)."""
        periods = line.period_ids.sorted("date_start")
        for period in periods:
            period.amount_planned = 0.0
        periods[period_index].amount_planned = total

    def _fresh(self, line):
        line.invalidate_recordset()
        self.env.invalidate_all()
        return line

    def test_a_dated_order_switches_the_basis_to_calendar(self):
        """Plan entièrement VENTILÉ SUR LE PASSÉ : le reliquat non couvert est
        dû en entier, et il suit la ventilation (1 100 $), pas un prorata
        linéaire de l'exercice (qui aurait rendu 700 $)."""
        line = self._window_line()
        self._ventilate_all(line, 0)
        self._po(300.0, date=self.today - relativedelta(days=10))
        self._po(100.0, date=self.today + relativedelta(days=50))
        line = self._fresh(line)
        self.assertEqual(line.purchase_period_total, 400.0)
        self.assertEqual(line.purchase_due_to_date, 300.0)
        self.assertEqual(line.theoretical_basis, "calendar")
        self.assertAlmostEqual(line.amount_theoretical, 300.0 + 800.0, places=2)

    def test_a_seasonal_plan_is_not_flattened_by_a_small_order(self):
        """🔴 Le piège que `theoretical_basis` dénonce : un poste ventilé sur la
        FIN de l'exercice, plus un petit engagement daté, ne doit pas lire la
        moitié du plan comme déjà due. L'ancien reliquat linéaire aurait rendu
        ≈ 750 $ et masqué toute dérive réelle du poste."""
        line = self._window_line()
        self._ventilate_all(line, -1)
        self._po(300.0, date=self.today - relativedelta(days=10))
        line = self._fresh(line)
        self.assertEqual(line.theoretical_basis, "calendar")
        self.assertAlmostEqual(line.amount_theoretical, 300.0, places=2)

    def test_without_orders_the_basis_stays_prorata(self):
        line = self._window_line()
        line = self._fresh(line)
        self.assertEqual(line.theoretical_basis, "prorata")

    def test_the_calendar_describes_the_plan_not_the_billing(self):
        """Une commande déjà facturée reste DUE au calendrier : le théorique dit
        ce qui devait être dépensé à ce jour, et ça l'a été."""
        line = self._window_line()
        self._ventilate_all(line, 0)
        order = self._po(300.0, date=self.today - relativedelta(days=10))
        self._bill(order)
        line = self._fresh(line)
        self.assertEqual(line.purchase_committed, 0.0)
        self.assertEqual(line.purchase_due_to_date, 300.0)
        self.assertEqual(line.theoretical_basis, "calendar")
        self.assertAlmostEqual(line.amount_theoretical, 300.0 + 900.0, places=2)

    def test_two_calendars_do_not_count_the_remainder_twice(self):
        """🔴 L'anti-double-comptage de la combinaison des calendriers.

        Avant bf_budget 18.0.1.4.0, chaque satellite calculait son théorique en
        proratant « le plan moins MON total » : abonnements et commandes
        ensemble auraient compté deux fois la part de l'autre dans le reliquat
        (ici : 240 + 300 + 960 au lieu de 240 + 300 + 660).
        """
        if "subscription.subscription" not in self.env:
            self.skipTest("bf_budget_subscription absent du registre")
        line = self._window_line()
        self._ventilate_all(line, 0)
        self.env["subscription.subscription"].create(
            {
                "code": "TEST-PO-SUB-0001",
                "name": "Abonnement ponctuel",
                "company_id": self.company.id,
                "currency_id": self.currency.id,
                "vendor_id": self.vendor.id,
                "category": "software_saas",
                "state": "active",
                "cycle": "one_time",
                "cycle_amount": 240.0,
                "start_date": self.today - relativedelta(days=20),
                "budget_position_id": self.position_software.id,
            }
        )
        self._po(300.0, date=self.today - relativedelta(days=10))
        line = self._fresh(line)
        self.assertEqual(line.theoretical_basis, "calendar")
        self.assertAlmostEqual(
            line.amount_theoretical, 240.0 + 300.0 + 660.0, places=2
        )

    def test_calendars_larger_than_the_plan_leave_no_negative_remainder(self):
        line = self._window_line(planned=200.0)
        self._po(300.0, date=self.today - relativedelta(days=10))
        line = self._fresh(line)
        self.assertEqual(line.amount_theoretical, 300.0)

    def test_an_order_due_exactly_today_is_due(self):
        """La frontière du dû est INCLUSIVE : une échéance d'aujourd'hui est due."""
        line = self._window_line()
        self._po(300.0, date=self.today)
        line = self._fresh(line)
        self.assertEqual(line.purchase_due_to_date, 300.0)

    def test_a_rebate_line_does_not_deform_the_calendar(self):
        """Une ligne de rabais (montant négatif) ne retranche ni du total ni du
        dû du calendrier : elle gonflerait le reliquat au-delà du plan et
        pourrait taire le composant alors qu'une ligne est échue."""
        line = self._window_line()
        self._ventilate_all(line, -1)
        self._po(300.0, date=self.today - relativedelta(days=10))
        self._po(-300.0, date=self.today - relativedelta(days=10))
        line = self._fresh(line)
        self.assertEqual(line.purchase_period_total, 300.0)
        self.assertEqual(line.purchase_due_to_date, 300.0)
        self.assertEqual(line.purchase_committed, 300.0)
        self.assertEqual(line.theoretical_basis, "calendar")
        self.assertAlmostEqual(line.amount_theoretical, 300.0, places=2)

    def test_a_zero_total_keeps_the_prorata_basis(self):
        """Une commande gratuite n'est pas un calendrier : rien à dater."""
        line = self._window_line()
        self._po(0.0, date=self.today - relativedelta(days=10))
        line = self._fresh(line)
        self.assertEqual(line.theoretical_basis, "prorata")

    def test_a_fully_billed_foreign_order_leaves_no_phantom_commitment(self):
        """🔴 Le reste se mesure dans la DEVISE DE LA COMMANDE, jamais entre deux
        conversions faites à des dates différentes : une commande étrangère
        entièrement facturée à un autre taux garderait sinon un reste fantôme
        (ici 100 $, affiché « 1 bon de commande ouvert » pour toujours)."""
        currency = self.env["res.currency"].create(
            {"name": "XBF", "symbol": "¤", "rounding": 0.01}
        )
        self.env["res.currency.rate"].create(
            [
                {
                    "name": self.today - relativedelta(days=10),
                    "currency_id": currency.id,
                    "company_id": self.company.id,
                    "rate": 2.0,
                },
                {
                    "name": self.today,
                    "currency_id": currency.id,
                    "company_id": self.company.id,
                    "rate": 2.5,
                },
            ]
        )
        line = self._window_line()
        order = self._po(
            1000.0, currency=currency, date=self.today - relativedelta(days=10)
        )
        line = self._fresh(line)
        self.assertEqual(line.purchase_committed, 500.0)
        self._bill(order)
        line = self._fresh(line)
        self.assertEqual(line.purchase_committed, 0.0)
        self.assertEqual(line.purchase_committed_count, 0)
        self.assertEqual(line.amount_actual, 400.0)
        self.assertEqual(line.amount_committed, 400.0)

    def test_action_view_purchase_orders_opens_the_window_orders(self):
        line = self._window_line()
        inside = self._po(300.0, date=self.today - relativedelta(days=10))
        outside = self._po(500.0, date=self.window_end + relativedelta(days=30))
        action = line.action_view_purchase_orders()
        ids = action["domain"][0][2]
        self.assertIn(inside.id, ids)
        self.assertNotIn(outside.id, ids)
