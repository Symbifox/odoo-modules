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

    def test_a_narrowed_line_carries_nothing_from_the_register(self):
        """🔴 Le registre ne sait rien de l'axe analytique.

        Deux lignes du même poste, l'une restreinte à un compte analytique :
        l'abonnement entier va à la ligne nue, RIEN à la restreinte — sinon le
        même renouvellement compterait sur chaque ligne restreinte du poste,
        et son réalisé n'arriverait que pondéré : l'engagé sauterait à la
        comptabilisation. Ni engagement, ni calendrier, ni angle mort.
        """
        sub = self._make_subscription("monthly", 100.0)
        budget, plain = self._line_with(sub)
        narrowed = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "position_id": self.position_software.id,
                "analytic_account_ids": [Command.set(self.analytic_a.ids)],
                "amount_planned": 600.0,
            }
        )
        budget.invalidate_recordset()
        self.assertTrue(plain.subscription_count)
        self.assertTrue(plain.subscription_period_total)
        self.assertEqual(narrowed.subscription_count, 0)
        self.assertEqual(narrowed.subscription_upcoming, 0.0)
        self.assertEqual(narrowed.subscription_period_total, 0.0)
        self.assertEqual(narrowed.theoretical_basis, "prorata")
        self.assertFalse(narrowed.has_subscription_without_calendar)

    def test_a_narrowed_line_declares_the_register_it_does_not_carry(self):
        """🔴 Le zéro d'une ligne restreinte se DÉCLARE, sinon il est muet.

        À `subscription_count` = 0, le bloc « Recurring commitments » ET la
        bannière de l'angle mort passent `invisible` : le lecteur d'une ligne
        restreinte ne voit plus que son poste porte un registre, et il ne peut
        pas savoir que l'engagé qu'il lit en est amputé. Le signal dit
        l'existence du registre et le fait que cette ligne ne le porte pas —
        jamais une part devinée.

        ⛔ Et ce n'est PAS `has_subscription_without_calendar` : celui-là dit
        qu'un abonnement dépense sans échéancier. Les confondre ferait crier
        l'angle mort du calendrier sur une ligne dont le calendrier n'est
        simplement pas le sien.
        """
        sub = self._make_subscription("monthly", 100.0)
        budget, plain = self._line_with(sub)
        narrowed = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "position_id": self.position_software.id,
                "analytic_account_ids": [Command.set(self.analytic_a.ids)],
                "amount_planned": 600.0,
            }
        )
        budget.invalidate_recordset()
        self.assertEqual(narrowed.subscription_count, 0)
        self.assertTrue(narrowed.has_unattributed_subscriptions)
        self.assertEqual(narrowed.position_subscription_count, 1)
        self.assertFalse(narrowed.has_subscription_without_calendar)
        # La ligne nue PORTE le registre : elle n'a rien à déclarer.
        self.assertTrue(plain.subscription_count)
        self.assertFalse(plain.has_unattributed_subscriptions)

    def test_a_narrowed_line_on_a_bare_budget_item_declares_nothing(self):
        """Pas de signal sans registre : une bannière qui s'allume toujours ne
        dit plus rien. Le poste des télécoms ne porte aucun abonnement."""
        self._make_subscription("monthly", 100.0).budget_position_id = (
            self.position_software
        )
        budget = self._make_budget(positions=[self.position_telecom])
        narrowed = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "position_id": self.position_telecom.id,
                "analytic_account_ids": [Command.set(self.analytic_a.ids)],
                "amount_planned": 600.0,
            }
        )
        internal = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "source": "internal_cost",
                "analytic_account_ids": [Command.set(self.analytic_a.ids)],
                "amount_planned": 300.0,
            }
        )
        budget.invalidate_recordset()
        self.assertFalse(narrowed.has_unattributed_subscriptions)
        self.assertEqual(narrowed.position_subscription_count, 0)
        # Une ligne de coût interne n'a pas de poste : le registre ne la
        # concerne pas, et elle ne doit rien déclarer non plus.
        self.assertFalse(internal.has_unattributed_subscriptions)
        self.assertEqual(internal.position_subscription_count, 0)

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
        pas voir l'autre moitié disparaître du théorique.

        ⚠️ Fenêtre posée AUTOUR d'aujourd'hui (pas l'exercice civil) : le
        31 décembre, le prorata d'un exercice civil vaut le plan ENTIER et la
        borne stricte « < 1200 » tombait ce jour-là. Avec un premier mois
        entièrement passé et un dernier entièrement à venir, le prorata est
        strictement entre 0 et le plan quel que soit le jour d'exécution.
        """
        today = fields.Date.context_today(self.env.user)
        sub = self._make_subscription(
            "one_time", 600.0, start=today - relativedelta(days=20)
        )
        sub.budget_position_id = self.position_software
        budget = self.env["bf.budget"].create(
            {
                "name": "Fenêtre autour d'aujourd'hui",
                "company_id": self.company.id,
                "date_start": today - relativedelta(days=99),
                "date_end": today + relativedelta(days=100),
            }
        )
        line = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "position_id": self.position_software.id,
                "amount_planned": 1200.0,
            }
        )
        budget.action_open()
        line.invalidate_recordset()
        self.assertEqual(line.theoretical_basis, "calendar")
        self.assertGreater(line.amount_theoretical, 600.0)
        self.assertLess(line.amount_theoretical, 1200.0)

    def test_a_calendar_that_contributes_nothing_does_not_flip_the_basis(self):
        """Un ponctuel d'un exercice PASSÉ, encore lié au poste, déclarait un
        calendrier vide (0, 0) : la base passait en « calendar » et le reliquat
        remplaçait la ventilation, sans qu'aucune échéance ne date le poste."""
        sub = self._make_subscription(
            "one_time", 80.0, start=self.date_start - relativedelta(days=30)
        )
        budget, line = self._line_with(sub)
        budget.action_open()
        line.invalidate_recordset()
        self.assertEqual(line.subscription_period_total, 0.0)
        self.assertEqual(line.theoretical_basis, "prorata")

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
