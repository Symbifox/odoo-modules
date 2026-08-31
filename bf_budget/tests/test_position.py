from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import BfBudgetCommon


@tagged("post_install", "-at_install")
class TestBfBudgetPosition(BfBudgetCommon):
    def test_position_counts_its_accounts(self):
        self.assertEqual(self.position_software.account_count, 1)

    def test_expense_position_refuses_income_account(self):
        """Le sens du poste décide du signe : un compte de produit y est un piège."""
        with self.assertRaises(ValidationError):
            self.position_software.write(
                {"account_ids": [Command.link(self.account_revenue.id)]}
            )

    def test_position_refuses_foreign_company_account(self):
        other = self.env["res.company"].create({"name": "Autre société"})
        foreign = (
            self.env["account.account"]
            .with_company(other)
            .create({"name": "Charge ailleurs", "code": "699999", "account_type": "expense"})
        )
        with self.assertRaises(ValidationError):
            self.env["bf.budget.position"].create(
                {
                    "name": "Poste étranger",
                    "code": "FOREIGN",
                    "budget_type": "expense",
                    "company_id": self.company.id,
                    "account_ids": [Command.set(foreign.ids)],
                }
            )

    def test_code_is_unique_per_company(self):
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self.env["bf.budget.position"].create(
                    {
                        "name": "Doublon",
                        "code": "SOFT",
                        "budget_type": "expense",
                        "company_id": self.company.id,
                        "account_ids": [Command.set(self.account_telecom.ids)],
                    }
                )

    def test_wizard_proposes_from_the_real_chart(self):
        """L'assistant part du plan comptable, pas d'un catalogue semé."""
        self.position_software.unlink()
        self.position_telecom.unlink()
        wizard = self.env["bf.budget.position.wizard"].create(
            {"company_id": self.company.id, "budget_type": "expense"}
        )
        self.assertIn("compte", wizard.preview)
        wizard.action_create()
        created = self.env["bf.budget.position"].search(
            [("company_id", "=", self.company.id), ("budget_type", "=", "expense")]
        )
        self.assertTrue(created)
        # Les deux comptes de charge tombent dans le même poste OPEX : trois à
        # cinq postes, jamais un poste par compte.
        opex = created.filtered(lambda p: p.code == "OPEX")
        self.assertEqual(len(opex), 1)
        self.assertEqual(
            set(opex.account_ids.ids),
            {self.account_software.id, self.account_telecom.id},
        )

    def test_wizard_refuses_to_create_twice(self):
        # Le décor porte déjà un poste de produits : l'assistant n'aurait rien à
        # créer dès le premier appel, et le test ne prouverait rien.
        self.position_revenue.unlink()
        wizard = self.env["bf.budget.position.wizard"].create(
            {"company_id": self.company.id, "budget_type": "revenue"}
        )
        wizard.action_create()
        with self.assertRaises(UserError):
            wizard.action_create()
