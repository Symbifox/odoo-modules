from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestExpenseBridge(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env["hr.employee"])
        cls.company = cls.env.company
        cls.employee = cls.env["hr.employee"].create({
            "name": "Dépensière", "company_id": cls.company.id,
        })
        cls.benefit = cls.env["bf.ex.benefit"].create({
            "name": "Budget de formation (essai)",
            "company_id": cls.company.id,
            "category": "learning",
            "cost_model": "per_use",
        })
        cls.product = cls.env["product.product"].create({
            "name": "Formation (essai)",
            "can_be_expensed": True,
            "type": "service",
            "standard_price": 0.0,
        })

    def _expense(self, amount=850.0, product=None):
        return self.env["hr.expense"].create({
            "name": "Cours d'appoint",
            "employee_id": self.employee.id,
            "product_id": (product or self.product).id,
            "total_amount_currency": amount,
            "date": self.today,
            "company_id": self.company.id,
        })

    def test_benefit_defaults_from_the_product(self):
        """On rattache le produit une fois, les dépenses suivent."""
        self.product.product_tmpl_id.ex_benefit_id = self.benefit
        expense = self._expense()
        self.assertEqual(expense.ex_benefit_id, self.benefit)

    def test_approval_creates_a_confirmed_usage_at_the_real_cost(self):
        expense = self._expense(amount=850.0)
        expense.ex_benefit_id = self.benefit
        self.assertFalse(expense.ex_usage_id)

        expense.write({"state": "approved"})

        usage = expense.ex_usage_id
        self.assertTrue(usage, "l'approbation doit produire une ligne d'usage")
        self.assertEqual(usage.state, "confirmed")
        self.assertEqual(usage.source, "expense")
        self.assertEqual(usage.employee_id, self.employee)
        self.assertEqual(usage.benefit_id, self.benefit)
        self.assertAlmostEqual(usage.amount, 850.0)

    def test_no_duplicate_usage(self):
        """⚠️ `state` est un champ CALCULÉ sur hr.expense : il peut être
        recalculé plusieurs fois pour la même transition. Le garde est
        `ex_usage_id`, pas la transition."""
        expense = self._expense()
        expense.ex_benefit_id = self.benefit
        expense.write({"state": "approved"})
        first = expense.ex_usage_id
        expense.write({"state": "done"})
        expense.write({"state": "approved"})
        self.assertEqual(expense.ex_usage_id, first)
        self.assertEqual(
            self.env["bf.ex.usage"].search_count([("benefit_id", "=", self.benefit.id)]), 1
        )

    def test_expense_without_benefit_creates_nothing(self):
        expense = self._expense()
        expense.write({"state": "approved"})
        self.assertFalse(expense.ex_usage_id)

    def test_usage_without_right_is_flagged_not_blocked(self):
        """Le pont ne bloque rien : il signale, comme une saisie manuelle."""
        expense = self._expense()
        expense.ex_benefit_id = self.benefit
        expense.write({"state": "approved"})
        self.assertTrue(expense.ex_usage_id)
        self.assertFalse(expense.ex_usage_id.entitled)

    def test_usage_is_entitled_when_the_right_is_open(self):
        self.env["bf.ex.entitlement"].create({
            "employee_id": self.employee.id,
            "benefit_id": self.benefit.id,
            "source": "manual",
            "reason": "Pour l'essai.",
        })
        expense = self._expense()
        expense.ex_benefit_id = self.benefit
        expense.write({"state": "approved"})
        self.assertTrue(expense.ex_usage_id.entitled)

    def test_real_cost_reaches_the_benefit(self):
        """Le but du pont : le coût cesse d'être une estimation."""
        self.env["bf.ex.entitlement"].create({
            "employee_id": self.employee.id, "benefit_id": self.benefit.id,
            "source": "manual", "reason": "Pour l'essai.",
        })
        expense = self._expense(amount=1275.0)
        expense.ex_benefit_id = self.benefit
        expense.write({"state": "approved"})
        self.benefit.invalidate_recordset()
        self.assertAlmostEqual(self.benefit.annual_cost, 1275.0)
        self.assertEqual(self.benefit.user_count, 1)
