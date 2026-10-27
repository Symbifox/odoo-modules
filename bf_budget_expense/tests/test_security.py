from odoo.tests import new_test_user, tagged

from .common import BfBudgetExpenseCommon


@tagged("post_install", "-at_install")
class TestExpenseBudgetSecurity(BfBudgetExpenseCommon):
    """⚠️ Sous de VRAIS comptes, jamais uid 1, et sans lecture sudo avant.

    Le cache de l'ORM est par transaction : un montant lu d'abord en sudo serait
    resservi tel quel au compte sans droits, et le test passerait sans rien
    prouver. Chaque test lit donc sous le rôle EN PREMIER, et compare à un
    chiffre écrit en dur.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.reader = new_test_user(
            cls.env,
            login="budget_expense_reader",
            groups="base.group_user,bf_budget.group_bf_budget_user",
            company_id=cls.company.id,
            company_ids=[(6, 0, [cls.company.id])],
        )

    def test_a_budget_reader_sees_the_committed_amount_without_expense_rights(self):
        """🔴 Sans sudo, la règle d'Odoo ne laisserait voir que SES notes.

        Le montant serait faux, et rien ne le signalerait : un engagé à zéro a
        l'air d'un poste sans note en attente.
        """
        line = self._line()
        self._sheet(110.0, tax=self.tax_recoverable)
        self._sheet(55.0, stage="submit")
        line.invalidate_recordset()
        self.env.invalidate_all()
        as_reader = line.with_user(self.reader)
        self.assertEqual(as_reader.expense_committed, 100.0)
        self.assertEqual(as_reader.amount_committed, 100.0)
        self.assertEqual(as_reader.expense_pending, 55.0)
        # Et il ne voit pas les notes elles-mêmes : la ligne n'expose que des sommes.
        self.assertFalse(self.env["hr.expense"].with_user(self.reader).search([]))

    def test_the_line_form_loads_for_a_budget_reader(self):
        views = self.env["bf.budget.line"].with_user(self.reader).get_views([(False, "form")])
        arch = views["views"]["form"]["arch"]
        self.assertIn("expense_committed", arch)
        # Le bouton qui ouvre les notes est réservé à qui peut les approuver.
        self.assertNotIn("action_view_expenses", arch)
        line = self._line()
        line.invalidate_recordset()
        self.env.invalidate_all()
        values = line.with_user(self.reader).read(
            ["expense_committed", "expense_committed_count", "expense_pending",
             "expense_pending_count", "amount_committed"]
        )
        self.assertEqual(values[0]["expense_committed"], 0.0)
