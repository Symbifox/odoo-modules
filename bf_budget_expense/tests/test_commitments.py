from odoo import Command
from odoo.tests import tagged

from .common import BfBudgetExpenseCommon


@tagged("post_install", "-at_install")
class TestExpenseCommitments(BfBudgetExpenseCommon):
    def _fresh(self, line):
        line.invalidate_recordset()
        return line

    def test_an_approved_expense_is_committed_without_its_recoverable_tax(self):
        """Une note approuvée est une dépense décidée : elle est engagée.

        Et seulement pour sa part de charge : la taxe récupérable part sur son
        propre compte, elle n'est pas une dépense du poste.
        """
        line = self._line()
        self._sheet(110.0, tax=self.tax_recoverable)
        line = self._fresh(line)
        self.assertEqual(line.amount_actual, 0.0)
        self.assertEqual(line.expense_committed, 100.0)
        self.assertEqual(line.expense_committed_count, 1)
        self.assertEqual(line.amount_committed, 100.0)

    def test_posting_moves_the_amount_from_committed_to_actual(self):
        """🔴 Le garde contre le double comptage : le lien, jamais l'état.

        Une fois l'écriture comptabilisée, la note est dans le réalisé. Elle doit
        sortir de l'engagé supplémentaire au même instant, sinon l'engagé
        compterait deux fois la même dépense.
        """
        line = self._line()
        sheet = self._sheet(110.0, tax=self.tax_recoverable)
        before = self._fresh(line).amount_committed
        sheet.action_sheet_move_post()
        line = self._fresh(line)
        # Le piège, constaté plutôt que supposé : comptabilisée mais non payée,
        # la note garde l'état « approved ». Un filtre sur l'état la compterait.
        self.assertEqual(sheet.state, "post")
        self.assertEqual(sheet.expense_line_ids.state, "approved")
        self.assertEqual(line.amount_actual, 100.0)
        self.assertEqual(line.expense_committed, 0.0)
        self.assertEqual(line.amount_committed, before)

    def test_a_submitted_expense_is_shown_but_not_committed(self):
        """Une note que personne n'a approuvée n'est pas une décision."""
        line = self._line()
        self._sheet(110.0, tax=self.tax_recoverable, stage="submit")
        line = self._fresh(line)
        self.assertEqual(line.expense_pending, 100.0)
        self.assertEqual(line.expense_pending_count, 1)
        self.assertEqual(line.expense_committed, 0.0)
        self.assertEqual(line.amount_committed, line.amount_actual)

    def test_draft_and_refused_expenses_count_nowhere(self):
        line = self._line()
        self._sheet(55.0, stage="draft")
        refused = self._sheet(66.0, stage="submit")
        refused._do_refuse("Hors politique")
        self.assertEqual(refused.state, "cancel")
        line = self._fresh(line)
        self.assertEqual(line.expense_pending, 0.0)
        self.assertEqual(line.expense_committed, 0.0)

    def test_an_expense_lands_on_the_budget_item_of_its_account(self):
        software = self._line()
        telecom = self._line(position=self.position_telecom, budget=software.budget_id)
        self._sheet(77.0, account=self.account_telecom)
        self.assertEqual(self._fresh(software).expense_committed, 0.0)
        self.assertEqual(self._fresh(telecom).expense_committed, 77.0)

    def test_an_expense_booked_last_year_stays_out(self):
        line = self._line()
        sheet = self._sheet(88.0, date=self._last_year())
        self.assertLess(sheet.accounting_date, self.date_start)
        self.assertEqual(self._fresh(line).expense_committed, 0.0)

    def test_a_non_recoverable_tax_stays_in_the_budget(self):
        """Une taxe non récupérable est une charge : elle reste sur le poste.

        Soustraire le montant de taxe du total l'aurait fait disparaître.
        """
        line = self._line()
        sheet = self._sheet(110.0, tax=self.tax_non_recoverable)
        self.assertEqual(self._fresh(line).expense_committed, 110.0)
        sheet.action_sheet_move_post()
        line = self._fresh(line)
        self.assertEqual(line.amount_actual, 110.0)
        self.assertEqual(line.expense_committed, 0.0)

    def test_the_computed_amount_is_what_posting_will_book(self):
        """Le montant calculé sans écriture est celui que l'écriture portera.

        Une note soumise n'a pas encore d'écriture : son montant est calculé.
        Une fois approuvée puis comptabilisée, le réalisé doit retomber sur le
        même chiffre, pour les deux sortes de taxe.
        """
        line = self._line()
        for tax in (self.tax_recoverable, self.tax_non_recoverable):
            sheet = self._sheet(123.45, tax=tax, stage="submit")
            pending = self._fresh(line).expense_pending
            actual_before = line.amount_actual
            sheet.action_approve_expense_sheets()
            sheet.action_sheet_move_post()
            line = self._fresh(line)
            self.assertAlmostEqual(line.amount_actual - actual_before, pending, places=2)
            self.assertEqual(line.expense_pending, 0.0)

    def test_an_accountant_edit_on_the_draft_entry_is_followed(self):
        """L'écriture au brouillon fait foi, pas la note qui l'a produite.

        Un comptable qui corrige le compte avant de comptabiliser déplace la
        dépense. Lire la note aurait laissé l'engagé sur l'ancien poste, et le
        montant aurait sauté de poste au moment de la comptabilisation.
        """
        software = self._line()
        telecom = self._line(position=self.position_telecom, budget=software.budget_id)
        sheet = self._sheet(99.0)
        expense_line = sheet.account_move_ids.line_ids.filtered(
            lambda aml: aml.account_id == self.account_software
        )
        expense_line.account_id = self.account_telecom
        self.assertEqual(self._fresh(software).expense_committed, 0.0)
        self.assertEqual(self._fresh(telecom).expense_committed, 99.0)

    def test_an_approved_report_without_entry_falls_back_on_the_computation(self):
        """Écriture supprimée à la main : la note reste approuvée, et engagée."""
        line = self._line()
        sheet = self._sheet(110.0, tax=self.tax_recoverable)
        sheet.account_move_ids.unlink()
        self.assertEqual(sheet.approval_state, "approve")
        self.assertFalse(sheet.account_move_ids)
        line = self._fresh(line)
        self.assertEqual(line.expense_committed, 100.0)
        self.assertEqual(line.expense_committed_count, 1)

    def test_the_analytic_restriction_applies_to_expenses_too(self):
        line = self._line(analytic_account_ids=[Command.set(self.analytic_a.ids)])
        self._sheet(40.0, analytic=self.analytic_a)
        self._sheet(60.0, analytic=self.analytic_b)
        self.assertEqual(self._fresh(line).expense_committed, 40.0)

    def test_revenue_and_internal_cost_lines_ignore_expenses(self):
        self._sheet(50.0)
        revenue_budget = self._make_budget(budget_type="revenue", positions=[self.position_revenue])
        internal = self._line(
            source="internal_cost",
            position_id=False,
            analytic_account_ids=[Command.set(self.analytic_a.ids)],
        )
        self.assertEqual(self._fresh(revenue_budget.line_ids).expense_committed, 0.0)
        self.assertEqual(self._fresh(internal).expense_committed, 0.0)

    def test_the_view_button_lists_what_the_line_counts(self):
        line = self._line()
        approved = self._sheet(31.0)
        submitted = self._sheet(32.0, stage="submit")
        self._sheet(33.0, stage="post")
        action = line.action_view_expenses()
        listed = self.env["hr.expense"].search(action["domain"])
        self.assertEqual(listed, approved.expense_line_ids | submitted.expense_line_ids)
