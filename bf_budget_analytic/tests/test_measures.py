from odoo import Command
from odoo.tests import tagged

from .common import BfBudgetAnalyticCommon


@tagged("post_install", "-at_install")
class TestAnalyticMeasures(BfBudgetAnalyticCommon):
    def test_hours_and_dollars_follow_the_timesheets(self):
        budget, line = self._labour_line()
        self._timesheet(self.employee_anne, 2.0)   # 2 h × 40 $
        self._timesheet(self.employee_bob, 3.0)    # 3 h × 60 $
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 260.0)
        self.assertEqual(line.timesheet_hours, 5.0)
        self.assertEqual(line.analytic_line_count, 2)

    def test_hours_stay_visible_when_the_rate_is_missing(self):
        """Un taux horaire absent laisse les dollars à zéro, jamais les heures.

        C'est le constat fondateur du coût interne : un coût à 0,00 $ qui ne se
        dénonce pas se lit comme du travail gratuit.
        """
        self.employee_bob.hourly_cost = 0.0
        budget, line = self._labour_line()
        self._timesheet(self.employee_bob, 4.0)
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 0.0)
        self.assertEqual(line.timesheet_hours, 4.0)
        self.assertEqual(line.unvalued_hours, 4.0)

    def test_hours_follow_the_window(self):
        budget, line = self._labour_line()
        self._timesheet(self.employee_anne, 2.0)
        self._timesheet(
            self.employee_anne,
            7.0,
            date=self.date_start.replace(year=self.date_start.year - 1),
        )
        line.invalidate_recordset()
        self.assertEqual(line.timesheet_hours, 2.0)
        self.assertEqual(line.analytic_line_count, 1)

    def test_hours_count_only_timesheets_never_manual_entries(self):
        """Les heures sont des heures de FEUILLES DE TEMPS.

        Une écriture analytique saisie à la main peut porter des heures
        (`unit_amount`) sans projet : elle compte dans les dollars du coût
        interne, jamais dans les heures de feuilles de temps.
        """
        budget, line = self._labour_line()
        self._timesheet(self.employee_anne, 5.0)
        manual = self._internal_cost(self.analytic_work, 90.0)
        manual.unit_amount = 9.0
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 290.0)  # 5 h × 40 $ + 90 $
        self.assertEqual(line.timesheet_hours, 5.0)
        self.assertEqual(line.analytic_line_count, 2)

    def test_a_pure_ledger_line_measures_nothing_analytic(self):
        """Une ligne comptable sans filtre n'a ni compte ni heures analytiques."""
        budget = self._make_budget()
        line = budget.line_ids
        self._post_bill(self.account_software, 300.0, analytic=self.analytic_a)
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 300.0)
        self.assertEqual(line.analytic_line_count, 0)
        self.assertEqual(line.timesheet_hours, 0.0)

    def test_the_count_follows_the_measure_for_a_narrowed_accounting_line(self):
        """Le compte du dépliage est celui de la mesure : les PARTS, pas les pièces."""
        budget = self.env["bf.budget"].create(
            {
                "name": "Parts comptables",
                "company_id": self.company.id,
                "date_start": self.date_start,
                "date_end": self.date_end,
            }
        )
        line = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "position_id": self.position_software.id,
                "analytic_account_ids": [Command.set(self.analytic_a.ids)],
                "amount_planned": 600.0,
            }
        )
        self._post_bill(
            self.account_software,
            1000.0,
            distribution={self.analytic_a: 50.0, self.analytic_b: 50.0},
        )
        # Une écriture analytique SAISIE À LA MAIN sur le même compte n'entre
        # pas dans une ligne comptable : autre source, autre mesure.
        self._internal_cost(self.analytic_a, 150.0)
        line.invalidate_recordset()
        self.assertEqual(line.amount_actual, 500.0)
        self.assertEqual(line.analytic_line_count, 1)
        self.assertEqual(line.timesheet_hours, 0.0)
        # Le compteur compte les PARTS, pas les pièces : une ligne qui
        # surveille les deux comptes voit DEUX parts d'une seule écriture.
        both = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "position_id": self.position_software.id,
                "analytic_account_ids": [
                    Command.set((self.analytic_a | self.analytic_b).ids)
                ],
                "amount_planned": 1200.0,
            }
        )
        both.invalidate_recordset()
        self.assertEqual(both.amount_actual, 1000.0)
        self.assertEqual(both.analytic_line_count, 2)

    def test_a_budget_counts_each_dollar_once_across_the_two_sources(self):
        """🔴 Le test anti-double-comptage du §9, par la vraie chaîne RH.

        Une facture ventilée sur le compte du projet produit une écriture ET une
        ligne analytique adossée ; les feuilles de temps n'ont pas de pièce. Le
        budget qui porte les deux lignes (comptable sans filtre + coût interne)
        compte chaque dollar une fois : 800 + 260, jamais 800 + 800 + 260.
        """
        self._timesheet(self.employee_anne, 2.0)   # 80 $
        self._timesheet(self.employee_bob, 3.0)    # 180 $
        self._post_bill(
            self.account_software, 800.0, distribution={self.analytic_work: 100.0}
        )
        budget = self.env["bf.budget"].create(
            {
                "name": "Deux sources, un seul compte",
                "company_id": self.company.id,
                "date_start": self.date_start,
                "date_end": self.date_end,
            }
        )
        accounting = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "position_id": self.position_software.id,
                "amount_planned": 1000.0,
            }
        )
        internal = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "source": "internal_cost",
                "analytic_account_ids": [Command.set(self.analytic_work.ids)],
                "amount_planned": 1000.0,
            }
        )
        budget.invalidate_recordset()
        self.assertEqual(accounting.amount_actual, 800.0)
        self.assertEqual(internal.amount_actual, 260.0)
        self.assertEqual(budget.amount_actual, 1060.0)
        # La ligne de coût interne ne compte ni la part analytique de la
        # facture, ni ses heures : la part est adossée à une pièce.
        self.assertEqual(internal.analytic_line_count, 2)
        self.assertEqual(internal.timesheet_hours, 5.0)
