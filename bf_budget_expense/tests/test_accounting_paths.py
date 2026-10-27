from odoo.tests import tagged

from .common import BfBudgetExpenseCommon


@tagged("post_install", "-at_install")
class TestExpenseAccountingPaths(BfBudgetExpenseCommon):
    """Les chemins que la comptabilité fait prendre à une note après coup.

    🔴 Avoir et extourne : Odoo EFFACE `expense_sheet_id` sur la facture d'une
    note dès qu'on lui fait un avoir. La pièce redevient « approuvée sans
    écriture » alors que sa dépense est dans le réalisé. Un module qui se fiait
    au lien de la pièce la recomptait dans l'engagé, parfois pour de bon.
    """

    def _fresh(self, line):
        line.invalidate_recordset()
        self.env.invalidate_all()
        return line

    def _reverse(self, move, modify=False):
        wizard = (
            self.env["account.move.reversal"]
            .with_context(active_model="account.move", active_ids=move.ids)
            .create({"journal_id": move.journal_id.id, "date": move.date, "reason": "Correction"})
        )
        if modify:
            wizard.modify_moves()
        else:
            wizard.refund_moves()
        return wizard

    def _drafts_of(self, sheet):
        return self.env["account.move"].search(
            [("state", "=", "draft"), ("line_ids.expense_id", "in", sheet.expense_line_ids.ids)]
        )

    def test_a_credit_note_never_counts_the_expense_twice(self):
        """Avoir complet : 100 du début à la fin, jamais 200."""
        line = self._line()
        sheet = self._sheet(110.0, tax=self.tax_recoverable, stage="post")
        bill = sheet.account_move_ids
        self._reverse(bill)
        # Le piège, constaté : la facture a perdu sa pièce.
        self.assertFalse(sheet.account_move_ids)
        self.assertEqual(sheet.approval_state, "approve")
        line = self._fresh(line)
        self.assertEqual(line.amount_actual, 100.0)
        self.assertEqual(line.amount_committed, 100.0)
        refund = self._drafts_of(sheet)
        self.assertEqual(refund.move_type, "in_refund")
        refund.invoice_date = refund.invoice_date or bill.invoice_date
        refund.action_post()
        line = self._fresh(line)
        # Avoir comptabilisé, réel à zéro. La pièce reste « Approuvée » dans Odoo et
        # propose encore de comptabiliser : elle reste donc engagée, comme Odoo le dit.
        self.assertEqual(line.amount_actual, 0.0)
        self.assertEqual(line.amount_committed, 100.0)

    def test_reverse_and_redraft_counts_the_expense_once(self):
        """« Extourner et créer un brouillon » : le brouillon recréé n'a plus de pièce."""
        line = self._line()
        sheet = self._sheet(110.0, tax=self.tax_recoverable, stage="post")
        bill = sheet.account_move_ids
        self._reverse(bill, modify=True)
        redraft = self._drafts_of(sheet)
        self.assertEqual(len(redraft), 1)
        self.assertFalse(redraft.expense_sheet_id)
        line = self._fresh(line)
        self.assertEqual(line.amount_actual, 0.0)
        self.assertEqual(line.amount_committed, 100.0)
        redraft.invoice_date = redraft.invoice_date or bill.invoice_date
        redraft.action_post()
        line = self._fresh(line)
        self.assertEqual(line.amount_actual, 100.0)
        self.assertEqual(line.expense_committed, 0.0)
        self.assertEqual(line.amount_committed, 100.0)

    def test_a_duplicated_expense_bill_is_not_counted_again(self):
        """« Dupliquer » sur la facture d'une note : la copie garde les liens de ligne.

        Elle n'est ni un avoir ni le brouillon d'une extourne. Sa note est déjà
        portée, comptabilisée ou au brouillon de sa pièce : la copie ne compte pas.
        """
        line = self._line()
        posted = self._sheet(110.0, tax=self.tax_recoverable, stage="post")
        approved = self._sheet(220.0, tax=self.tax_recoverable)
        before = self._fresh(line)
        self.assertEqual(before.amount_actual, 100.0)
        self.assertEqual(before.expense_committed, 200.0)
        copies = posted.account_move_ids.copy() | approved.account_move_ids.copy()
        self.assertEqual(set(copies.mapped("state")), {"draft"})
        self.assertTrue(copies.line_ids.expense_id)
        line = self._fresh(line)
        self.assertEqual(line.amount_actual, 100.0)
        self.assertEqual(line.expense_committed, 200.0)
        self.assertEqual(line.amount_committed, 300.0)

    def test_a_partial_credit_note_leaves_only_what_was_spent(self):
        line = self._line()
        sheet = self._sheet(110.0, tax=self.tax_recoverable, stage="post")
        bill = sheet.account_move_ids
        self._reverse(bill)
        refund = self._drafts_of(sheet)
        # 33 taxes comprises, donc 30 de charge rendus sur les 100.
        refund.invoice_line_ids.filtered("expense_id").price_unit = 33.0
        refund.invoice_date = refund.invoice_date or bill.invoice_date
        refund.action_post()
        line = self._fresh(line)
        self.assertEqual(line.amount_actual, 70.0)
        self.assertEqual(line.amount_committed, 70.0)

    def test_reset_then_resubmit_shows_the_expense_again(self):
        """Remise à zéro : la facture et son extourne s'annulent, la note redevient visible."""
        line = self._line()
        sheet = self._sheet(110.0, tax=self.tax_recoverable, stage="post")
        sheet.action_reset_expense_sheets()
        sheet.action_submit_sheet()
        line = self._fresh(line)
        self.assertEqual(line.amount_actual, 0.0)
        self.assertEqual(line.expense_pending, 100.0)

    def test_an_expense_paid_by_the_company_follows_the_same_rule(self):
        line = self._line()
        sheet = self._sheet(110.0, tax=self.tax_recoverable, stage="submit", mode="company_account")
        self.assertEqual(self._fresh(line).expense_pending, 100.0)
        sheet.action_approve_expense_sheets()
        self.assertEqual(sheet.approval_state, "approve")
        line = self._fresh(line)
        self.assertEqual(line.expense_committed, 100.0)
        self.assertEqual(line.amount_actual, 0.0)
        sheet.action_sheet_move_post()
        line = self._fresh(line)
        self.assertEqual(line.amount_actual, 100.0)
        self.assertEqual(line.expense_committed, 0.0)

    def test_a_foreign_currency_expense_is_computed_as_it_will_be_booked(self):
        """Le soumis calculé retombe au cent près sur ce que la facture portera.

        Payée par l'employé, la facture est en devise de la société : les taxes
        se calculent sur le total converti, pas dans la devise de la note.
        """
        line = self._line()
        for total in (18.03, 41.11, 7.77, 99.99, 123.45, 64.07):
            sheet = self._sheet(
                total, tax=self.tax_recoverable, stage="submit", currency=self.foreign_currency
            )
            pending = self._fresh(line).expense_pending
            actual_before = line.amount_actual
            sheet.action_approve_expense_sheets()
            sheet.action_sheet_move_post()
            line = self._fresh(line)
            self.assertAlmostEqual(
                line.amount_actual - actual_before, pending, places=2, msg="total %s" % total
            )

    def test_a_submitted_expense_dated_last_year_stays_out(self):
        line = self._line()
        self._sheet(44.0, date=self._last_year(), stage="submit")
        self.assertEqual(self._fresh(line).expense_pending, 0.0)

    def test_a_composite_analytic_key_is_recognised(self):
        """Clé « a,c » d'une société à deux plans : les deux comptes y sont nommés."""
        plan_2 = self.env["account.analytic.plan"].create({"name": "Second axe budget"})
        analytic_c = self.env["account.analytic.account"].create(
            {"name": "Axe C", "plan_id": plan_2.id}
        )
        key = "%s,%s" % (self.analytic_a.id, analytic_c.id)
        line = self._line(analytic_account_ids=[(6, 0, analytic_c.ids)])
        self._sheet(40.0, distribution={key: 100.0})
        self._sheet(50.0, distribution={key: 100.0}, stage="submit")
        line = self._fresh(line)
        self.assertEqual(line.expense_committed, 40.0)
        self.assertEqual(line.expense_pending, 50.0)

    def test_another_company_expense_stays_out_even_on_a_shared_account(self):
        """Un compte peut être partagé entre sociétés : la société, elle, ne l'est pas."""
        other = self.env["res.company"].create({"name": "Autre société budget"})
        self.env.user.company_ids |= other
        other_env = self.env(context=dict(self.env.context, allowed_company_ids=[other.id]))
        for account in (self.account_software, self.account_payable):
            # Odoo 18 : un compte partagé porte un code par société, et la
            # contrainte se vérifie à la fin de l'écriture : les deux ensemble.
            account.with_company(other).write(
                {"company_ids": [(4, other.id)], "code": account.code}
            )
        journal = other_env["account.journal"].create(
            {"name": "Notes autre", "code": "NFAU", "type": "purchase", "company_id": other.id}
        )
        other.expense_journal_id = journal
        partner = other_env["res.partner"].create({"name": "Employé autre"})
        partner.with_company(other).property_account_payable_id = self.account_payable
        employee = other_env["hr.employee"].create(
            {"name": "Employé autre", "company_id": other.id, "work_contact_id": partner.id}
        )
        sheets = other_env["hr.expense.sheet"]
        for total in (45.0, 46.0):
            expense = other_env["hr.expense"].create(
                {
                    "name": "Note autre %s" % total,
                    "employee_id": employee.id,
                    "company_id": other.id,
                    "account_id": self.account_software.id,
                    "total_amount_currency": total,
                    "payment_mode": "own_account",
                }
            )
            sheets |= other_env["hr.expense.sheet"].create(
                {
                    "name": "Pièce autre %s" % total,
                    "employee_id": employee.id,
                    "company_id": other.id,
                    "expense_line_ids": [(6, 0, expense.ids)],
                }
            )
        sheets.action_submit_sheet()
        sheets[0].action_approve_expense_sheets()
        self.assertEqual(sheets[0].approval_state, "approve")
        self.assertIn(self.account_software, sheets[0].account_move_ids.line_ids.account_id)
        line = self._fresh(self._line())
        self.assertEqual(line.expense_committed, 0.0)
        self.assertEqual(line.expense_pending, 0.0)
