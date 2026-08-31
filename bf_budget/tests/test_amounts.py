from odoo import Command, fields
from odoo.tests import tagged

from .common import BfBudgetCommon


@tagged("post_install", "-at_install")
class TestBfBudgetAmounts(BfBudgetCommon):
    def test_actual_reads_only_posted_entries(self):
        budget = self._make_budget()
        line = budget.line_ids
        self._post_bill(self.account_software, 300.0)
        self._post_bill(self.account_software, 500.0, post=False)
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 300.0)

    def test_expense_actual_is_positive(self):
        """Une charge est un débit : on la rend « dépensé », positif."""
        budget = self._make_budget()
        self._post_bill(self.account_software, 250.0)
        budget.line_ids.invalidate_recordset()
        self.assertEqual(budget.line_ids.amount_actual, 250.0)

    def test_revenue_actual_is_positive_too(self):
        """Un produit est un crédit : son solde est négatif, on le retourne."""
        budget = self._make_budget(budget_type="revenue", positions=[self.position_revenue])
        self._post_revenue(900.0)
        budget.line_ids.invalidate_recordset()
        self.assertEqual(budget.line_ids.amount_actual, 900.0)

    def test_other_accounts_are_ignored(self):
        budget = self._make_budget()
        self._post_bill(self.account_telecom, 400.0)
        budget.line_ids.invalidate_recordset()
        self.assertEqual(budget.line_ids.amount_actual, 0.0)

    def test_entries_outside_the_period_are_ignored(self):
        budget = self._make_budget()
        self._post_bill(
            self.account_software, 700.0, date=self.date_start.replace(year=self.date_start.year - 1)
        )
        budget.line_ids.invalidate_recordset()
        self.assertEqual(budget.line_ids.amount_actual, 0.0)

    def test_analytic_narrowing_filters_the_accounting_read(self):
        budget = self._make_budget()
        line = budget.line_ids
        self._post_bill(self.account_software, 100.0, analytic=self.analytic_a)
        self._post_bill(self.account_software, 250.0, analytic=self.analytic_b)
        self._post_bill(self.account_software, 40.0)
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 390.0)
        line.write({"analytic_account_ids": [Command.set(self.analytic_a.ids)]})
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 100.0)

    def test_internal_cost_sees_only_analytic_lines_without_a_journal_item(self):
        """🔴 Le test qui empêche le double comptage.

        Une facture avec distribution analytique produit une écriture ET une ligne
        analytique. La ligne « coût interne » ne doit voir que ce que la
        comptabilité ne porte pas, sinon chaque dollar compterait deux fois.
        """
        self._post_bill(self.account_software, 800.0, analytic=self.analytic_a)
        self._internal_cost(self.analytic_a, 150.0)

        # La preuve que les deux mondes existent bien pour le même compte analytique.
        # ⚠️ La colonne se demande au plan : `account_id` n'est celle du premier
        # plan racine, et une base fraîche en porte déjà un.
        column = self.analytic_a.root_plan_id._column_name()
        analytic_lines = self.env["account.analytic.line"].search(
            [(column, "=", self.analytic_a.id)]
        )
        self.assertEqual(len(analytic_lines), 2)
        self.assertEqual(len(analytic_lines.filtered("move_line_id")), 1)

        budget = self.env["bf.budget"].create(
            {
                "name": "Deux sources",
                "company_id": self.company.id,
                "date_start": self.date_start,
                "date_end": self.date_end,
            }
        )
        accounting = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "position_id": self.position_software.id,
                "analytic_account_ids": [Command.set(self.analytic_a.ids)],
                "amount_planned": 1000.0,
            }
        )
        internal = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "source": "internal_cost",
                "analytic_account_ids": [Command.set(self.analytic_a.ids)],
                "amount_planned": 1000.0,
            }
        )
        budget.invalidate_recordset()
        self.assertEqual(accounting.amount_actual, 800.0)
        self.assertEqual(internal.amount_actual, 150.0)
        # Le total du budget est la somme, jamais 800 + 800 + 150.
        self.assertEqual(budget.amount_actual, 950.0)

        # 🔴 La contre-épreuve : c'est bien le garde `move_line_id = False` qui
        # discrimine, et rien d'autre. Sans lui, la ligne « coût interne » verrait
        # aussi la ligne analytique née de la facture, et les 800 $ seraient
        # comptés deux fois dans le même budget.
        naive = self.env["account.analytic.line"].search(
            [(column, "=", self.analytic_a.id)]
        )
        self.assertEqual(-sum(naive.mapped("amount")), 950.0)
        guarded = naive.filtered(lambda line: not line.move_line_id)
        self.assertEqual(-sum(guarded.mapped("amount")), 150.0)

    def test_committed_equals_actual_without_a_satellite(self):
        """Le socle ne connaît aucun engagement : il ne prétend pas le contraire."""
        budget = self._make_budget()
        self._post_bill(self.account_software, 500.0)
        budget.line_ids.invalidate_recordset()
        self.assertEqual(budget.line_ids.amount_committed, budget.line_ids.amount_actual)

    def test_theoretical_follows_the_monthly_split(self):
        """🔴 Le théorique n'est pas un prorata du temps écoulé sur l'exercice.

        Tout le prévu est posé sur janvier : dès février, le théorique vaut le
        total, alors qu'un prorata du temps écoulé n'en donnerait qu'un douzième.
        """
        budget = self._make_budget(planned=1200.0)
        line = budget.line_ids
        january = line.period_ids.sorted("date_start")[0]
        for period in line.period_ids:
            period.amount_planned = 0.0
        january.amount_planned = 1200.0
        line.invalidate_recordset()
        self.assertEqual(line.amount_planned, 1200.0)
        self.assertEqual(line.theoretical_basis, "prorata")
        today = fields.Date.context_today(self.env.user)
        if today.month > 1:
            self.assertEqual(line.amount_theoretical, 1200.0)
        else:
            self.assertLess(line.amount_theoretical, 1200.0)

    def test_theoretical_is_zero_before_the_exercise(self):
        budget = self._make_budget()
        future = self.date_start.replace(year=self.date_start.year + 2)
        budget.write({"date_start": future, "date_end": future.replace(month=12, day=31)})
        budget.line_ids.action_regenerate_periods()
        budget.line_ids.invalidate_recordset()
        self.assertEqual(budget.line_ids.amount_theoretical, 0.0)

    def test_alert_needs_both_the_percentage_and_the_floor(self):
        """Un pourcentage seul hurle sur les petits postes, un montant seul reste muet."""
        budget = self._make_budget(planned=1200.0, state="open")
        line = budget.line_ids
        for period in line.period_ids:
            period.amount_planned = 100.0
        # Dérive de 60 $ : bien au-delà de 10 %, mais sous le plancher de 250 $.
        self._post_bill(self.account_software, line.amount_theoretical + 60.0)
        line.invalidate_recordset()
        self.assertGreater(line.amount_drift, 0.0)
        self.assertFalse(line.is_alert)
        # Une dérive de 400 $ franchit les deux seuils.
        self._post_bill(self.account_software, 400.0)
        line.invalidate_recordset()
        self.assertTrue(line.is_alert)
        # Un dépassement assumé cesse d'être signalé, sans effacer la dérive.
        line.write({"overrun_accepted": True, "overrun_reason": "Renouvellement anticipé"})
        line.invalidate_recordset()
        self.assertFalse(line.is_alert)
        self.assertGreater(line.amount_drift, 0.0)

    def test_a_draft_budget_never_alerts(self):
        budget = self._make_budget(planned=120.0)
        self._post_bill(self.account_software, 9000.0)
        budget.line_ids.invalidate_recordset()
        self.assertFalse(budget.line_ids.is_alert)

    def test_coverage_reports_uncovered_and_duplicated_accounts(self):
        budget = self._make_budget(positions=[self.position_software])
        self.assertIn(self.account_telecom, budget.uncovered_account_ids)
        self.assertTrue(budget.coverage_warning)
        double = self.env["bf.budget.position"].create(
            {
                "name": "Logiciels bis",
                "code": "SOFT2",
                "budget_type": "expense",
                "company_id": self.company.id,
                "account_ids": [Command.set(self.account_software.ids)],
            }
        )
        self.env["bf.budget.line"].create(
            {"budget_id": budget.id, "position_id": double.id, "amount_planned": 10.0}
        )
        budget.invalidate_recordset()
        self.assertIn(self.account_software, budget.duplicated_account_ids)

    def test_report_rows_carry_the_month_and_the_cumulative(self):
        budget = self._make_budget(planned=1200.0, state="open")
        self._post_bill(self.account_software, 300.0)
        budget.invalidate_recordset()
        rows = budget._report_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["planned"], 1200.0)
        self.assertEqual(row["actual"], 300.0)
        self.assertEqual(row["month_planned"], 100.0)
        totals = budget._report_totals(rows)
        self.assertEqual(totals["planned"], 1200.0)

    def test_report_renders(self):
        """Le rapport se rend pour de vrai.

        ⚠️ En test, le moteur rend du HTML et non un PDF : on contrôle donc le
        contenu, pas le type de fichier.
        """
        budget = self._make_budget(planned=1200.0, state="open")
        self._post_bill(self.account_software, 300.0)
        budget.invalidate_recordset()
        report = self.env["ir.actions.report"]._render_qweb_html(
            "bf_budget.report_bf_budget", budget.ids
        )
        html = report[0].decode() if isinstance(report[0], bytes) else report[0]
        self.assertIn("Exercice test", html)
        self.assertIn("Licences logicielles", html)
        self.assertIn("Théorique", html)

    def test_alert_filter_is_searchable(self):
        """⚠️ Un filtre de vue sur un champ calculé NON stocké fait échouer
        l'installation (« Unsearchable field ») si le champ n'a pas de `search=`.
        Le défaut est invisible à la lecture du code : il se prouve ici.
        """
        budget = self._make_budget(planned=1200.0, state="open")
        line = budget.line_ids
        for period in line.period_ids:
            period.amount_planned = 100.0
        self._post_bill(self.account_software, line.amount_theoretical + 900.0)
        line.invalidate_recordset()
        self.assertTrue(line.is_alert)
        found = self.env["bf.budget.line"].search([("is_alert", "=", True)])
        self.assertIn(line, found)
        quiet = self.env["bf.budget.line"].search([("is_alert", "=", False)])
        self.assertNotIn(line, quiet)

    def test_hours_logged_but_valued_at_zero_are_signalled(self):
        """🔴 Zéro dépensé, ou un taux horaire qui manque ? La ligne doit le dire.

        Odoo valorise le temps à partir du coût horaire de l'employé. Sans ce
        taux, `amount` vaut 0 alors que les heures sont bien saisies, et la ligne
        afficherait « rien dépensé » sans que rien ne paraisse anormal.
        """
        column = self.analytic_a.root_plan_id._column_name()
        self.env["account.analytic.line"].create(
            {
                "name": "Temps non valorisé",
                column: self.analytic_a.id,
                "amount": 0.0,
                "unit_amount": 7.5,
                "date": fields.Date.context_today(self.env.user),
                "company_id": self.company.id,
            }
        )
        budget = self.env["bf.budget"].create(
            {
                "name": "Main-d'oeuvre",
                "company_id": self.company.id,
                "date_start": self.date_start,
                "date_end": self.date_end,
            }
        )
        line = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "source": "internal_cost",
                "analytic_account_ids": [Command.set(self.analytic_a.ids)],
                "amount_planned": 5000.0,
            }
        )
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 0.0)
        self.assertTrue(line.has_unvalued_time)
        self.assertEqual(line.unvalued_hours, 7.5)

        # Du temps correctement valorisé ne déclenche rien.
        self._internal_cost(self.analytic_a, 400.0)
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 400.0)
        self.assertTrue(line.has_unvalued_time)  # les 7,5 h sans taux restent signalées
