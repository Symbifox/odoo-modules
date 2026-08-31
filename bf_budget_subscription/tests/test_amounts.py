from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.tests import tagged

from .common import BfBudgetSubscriptionCommon


@tagged("post_install", "-at_install")
class TestSubscriptionAmounts(BfBudgetSubscriptionCommon):
    def _line_with(self, subscription, planned=1200.0):
        subscription.budget_position_id = self.position_software
        budget = self._make_budget(planned=planned)
        budget.line_ids.invalidate_recordset()
        return budget, budget.line_ids

    def test_the_line_finds_its_subscriptions_by_itself(self):
        """Rien à reporter dans les budgets : rattacher au poste suffit."""
        sub = self._make_subscription("monthly", 100.0)
        budget, line = self._line_with(sub)
        self.assertEqual(line.subscription_count, 1)
        self.assertIn(sub, line.subscription_ids)

    def test_an_internal_cost_line_never_picks_up_subscriptions(self):
        sub = self._make_subscription("monthly", 100.0)
        sub.budget_position_id = self.position_software
        budget = self.env["bf.budget"].create({
            "name": "Coût interne", "company_id": self.company.id,
            "date_start": self.date_start, "date_end": self.date_end,
        })
        line = self.env["bf.budget.line"].create({
            "budget_id": budget.id, "source": "internal_cost",
            "analytic_account_ids": [Command.set(self.analytic_a.ids)],
            "amount_planned": 500.0,
        })
        line.invalidate_recordset()
        self.assertEqual(line.subscription_count, 0)

    def test_committed_takes_only_what_is_still_to_come(self):
        """🔴 Le garde contre le double comptage le plus coûteux.

        L'échu est déjà dans le réalisé dès que la facture est comptabilisée.
        L'ajouter aussi à l'engagé doublerait la dépense la plus prévisible du
        budget, celle qu'on croit justement la mieux tenue.
        """
        sub = self._make_subscription("monthly", 100.0)
        budget, line = self._line_with(sub)
        today = fields.Date.context_today(self.env.user)
        ecoules = len(sub._budget_occurrences(self.date_start, min(today, self.date_end)))
        a_venir = 12 - ecoules
        self.assertEqual(line.subscription_period_total, 1200.0)
        self.assertEqual(line.subscription_due_to_date, ecoules * 100.0)
        self.assertEqual(line.subscription_upcoming, a_venir * 100.0)
        # L'engagé du socle vaut le réalisé ; le satellite y ajoute l'à-venir, pas l'échu.
        self.assertEqual(line.amount_committed, line.amount_actual + a_venir * 100.0)

    def test_a_posted_bill_does_not_get_counted_twice(self):
        """La facture d'une échéance passée est dans le réalisé, une seule fois."""
        sub = self._make_subscription("monthly", 100.0)
        budget, line = self._line_with(sub)
        self._vendor_bill(sub, self.account_software, 100.0, self.date_start)
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 100.0)
        today = fields.Date.context_today(self.env.user)
        a_venir = 12 - len(sub._budget_occurrences(self.date_start, min(today, self.date_end)))
        self.assertEqual(line.amount_committed, 100.0 + a_venir * 100.0)

    def test_the_theoretical_switches_to_the_calendar(self):
        sub = self._make_subscription("annual", 1200.0)
        budget, line = self._line_with(sub, planned=1200.0)
        budget.action_open()
        line.invalidate_recordset()
        self.assertEqual(line.theoretical_basis, "calendar")
        # Une échéance annuelle au 1er janvier est DUE en entier dès le 1er janvier.
        # Un prorata du temps écoulé n'en aurait vu qu'une fraction, et aurait
        # crié au dépassement à chaque renouvellement.
        self.assertEqual(line.amount_theoretical, 1200.0)

    def test_the_part_of_the_plan_no_engagement_covers_stays_prorata(self):
        """Un poste où les abonnements ne pèsent que la moitié du budget ne doit
        pas voir l'autre moitié disparaître du théorique."""
        sub = self._make_subscription("annual", 600.0)
        budget, line = self._line_with(sub, planned=1200.0)
        budget.action_open()
        line.invalidate_recordset()
        self.assertEqual(line.theoretical_basis, "calendar")
        self.assertGreater(line.amount_theoretical, 600.0)
        self.assertLess(line.amount_theoretical, 1200.0)

    def test_without_a_subscription_the_basis_stays_prorata(self):
        budget = self._make_budget(planned=1200.0)
        budget.action_open()
        budget.line_ids.invalidate_recordset()
        self.assertEqual(budget.line_ids.theoretical_basis, "prorata")

    def test_an_on_demand_subscription_is_flagged_not_silently_zero(self):
        """🔴 Un calendrier partiel pris pour complet sous-estime le théorique.

        Sans le drapeau, un poste servi par un abonnement à la demande afficherait
        un théorique bas, donc une dérive haute, et personne ne saurait que
        l'échéancier ne couvre pas tout.
        """
        sub = self._make_subscription("on_demand", 80.0)
        budget, line = self._line_with(sub)
        self.assertTrue(line.has_subscription_without_calendar)
        self.assertIn(sub, line.subscription_no_calendar_ids)
        self.assertEqual(line.subscription_period_total, 0.0)
        # Aucun engagement daté : la base reste le prorata, elle ne ment pas.
        budget.action_open()
        line.invalidate_recordset()
        self.assertEqual(line.theoretical_basis, "prorata")

    def test_a_cancelled_on_demand_is_not_a_blind_spot_anymore(self):
        sub = self._make_subscription("on_demand", 80.0, state="cancelled")
        budget, line = self._line_with(sub)
        self.assertFalse(line.has_subscription_without_calendar)

    def test_amounts_are_converted_into_the_budget_currency(self):
        devise = self.foreign_currency
        self.assertNotEqual(devise, self.currency, "sinon le test ne convertit rien")
        sub = self._make_subscription("annual", 200.0, currency_id=devise.id)
        budget, line = self._line_with(sub, planned=1000.0)
        line.invalidate_recordset()
        # 200 dans une devise cotée 2 pour 1 vaut 100 dans la devise de la société.
        self.assertAlmostEqual(line.subscription_period_total, 100.0, places=2)
