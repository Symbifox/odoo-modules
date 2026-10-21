"""Le pont, joué par le vrai parcours d'une note de frais.

🔴 Les essais de la 18.0.1.0.0 écrivaient `state` à la main sur la
dépense : `expense.write({"state": "approved"})`. En Odoo 18, `state` est un
champ CALCULÉ depuis la note de frais (`hr.expense.sheet`), et personne
n'écrit jamais cet état sur la dépense. Le pont passait ses essais et ne se
déclenchait jamais en production.

Ces essais passent donc par la porte publique : la dépense se déclare
(`action_submit_expenses`), la note se soumet (`action_submit_sheet`), et un
compte qui a le droit d'approuver l'approuve (`action_approve_expense_sheets`),
puis la refuse par l'assistant, ou la remet en brouillon. Aucun ne touche
`state` directement.

`TestExpenseCommon` d'Odoo fournit ce que le parcours exige vraiment : un plan
comptable, un journal d'achat, une personne qui dépense et une personne qui
approuve.
"""

from odoo import Command, fields
from odoo.tests.common import tagged

from odoo.addons.hr_expense.tests.common import TestExpenseCommon


@tagged("post_install", "-at_install")
class TestExpenseBridge(TestExpenseCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env["hr.employee"])
        cls.company = cls.company_data["company"]
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
            "property_account_expense_id": cls.company_data["default_account_expense"].id,
            "supplier_taxes_id": [Command.clear()],
        })
        cls.product.product_tmpl_id.ex_benefit_id = cls.benefit

    # ------------------------------------------------------------------
    # Le parcours, tel qu'un écran le joue
    # ------------------------------------------------------------------

    def _expense(self, amount=850.0, product=None, **extra):
        values = {
            "name": "Cours d'appoint",
            "employee_id": self.expense_employee.id,
            "product_id": (product or self.product).id,
            "total_amount_currency": amount,
            "tax_ids": [Command.clear()],
            "date": self.today,
            "company_id": self.company.id,
            "currency_id": self.company.currency_id.id,
        }
        values.update(extra)
        return self.env["hr.expense"].create(values)

    def _report(self, expense):
        """Déclarer la dépense dans une note, comme le bouton « Déclarer »."""
        action = expense.action_submit_expenses()
        sheet = self.env["hr.expense.sheet"].browse(action["res_id"])
        self.assertEqual(sheet.expense_line_ids, expense)
        return sheet

    def _submit_and_approve(self, sheet):
        sheet.action_submit_sheet()
        result = sheet.with_user(self.expense_user_manager).action_approve_expense_sheets()
        self.assertFalse(result, "aucun doublon attendu : l'approbation doit être directe")
        return sheet

    def _approved(self, amount=850.0, **extra):
        expense = self._expense(amount=amount, **extra)
        sheet = self._submit_and_approve(self._report(expense))
        self.assertEqual(expense.state, "approved")
        return expense, sheet

    def _refuse(self, sheet, user=None):
        wizard = self.env["hr.expense.refuse.wizard"].with_user(
            user or self.expense_user_manager,
        ).with_context(active_model="hr.expense.sheet", active_ids=sheet.ids).create({
            "reason": "Hors politique (essai)",
        })
        wizard.action_refuse()

    def _usages(self):
        return self.env["bf.ex.usage"].search([("benefit_id", "=", self.benefit.id)])

    # ------------------------------------------------------------------

    def test_benefit_defaults_from_the_product(self):
        """On rattache le produit une fois, les dépenses suivent."""
        expense = self._expense()
        self.assertEqual(expense.ex_benefit_id, self.benefit)

    def test_sheet_approval_creates_a_confirmed_usage_at_the_real_cost(self):
        """Le défaut corrigé : ceci ne produisait AUCUNE ligne."""
        expense = self._expense(amount=850.0)
        sheet = self._report(expense)
        sheet.action_submit_sheet()
        self.assertEqual(expense.state, "submitted")
        self.assertFalse(expense.ex_usage_id, "une dépense soumise n'est pas encore engagée")

        sheet.with_user(self.expense_user_manager).action_approve_expense_sheets()

        self.assertEqual(expense.state, "approved")
        usage = expense.ex_usage_id
        self.assertTrue(usage, "l'approbation de la note doit produire une ligne d'usage")
        self.assertEqual(usage.state, "confirmed")
        self.assertEqual(usage.source, "expense")
        self.assertEqual(usage.employee_id, self.expense_employee)
        self.assertEqual(usage.benefit_id, self.benefit)
        self.assertEqual(usage.date, self.today)
        self.assertAlmostEqual(usage.amount, 850.0)

    def test_duplicate_wizard_approval_also_creates_the_usage(self):
        """L'autre porte d'Odoo 18 : l'assistant des doublons approuve aussi."""
        first, _sheet = self._approved(amount=410.0)
        twin = self._expense(amount=410.0)
        sheet = self._report(twin)
        sheet.action_submit_sheet()
        action = sheet.with_user(self.expense_user_manager).action_approve_expense_sheets()
        self.assertEqual(action["res_model"], "hr.expense.approve.duplicate")
        self.assertFalse(twin.ex_usage_id, "rien tant que le doublon n'est pas tranché")
        self.env["hr.expense.approve.duplicate"].with_user(
            self.expense_user_manager,
        ).with_context(action["context"]).create({}).action_approve()
        self.assertEqual(twin.state, "approved")
        self.assertTrue(twin.ex_usage_id)
        self.assertNotEqual(twin.ex_usage_id, first.ex_usage_id)
        self.assertEqual(len(self._usages()), 2)

    def test_posting_and_paying_keep_a_single_usage(self):
        """⚠️ `state` est CALCULÉ : comptabiliser puis payer le recalcule
        plusieurs fois. Le garde est `ex_usage_id`, pas la transition."""
        expense, sheet = self._approved()
        first = expense.ex_usage_id
        self.assertTrue(first)
        sheet.action_sheet_move_post()
        self.assertEqual(sheet.state, "post")
        self.get_new_payment(sheet, sheet.total_amount)
        self.assertEqual(expense.state, "done")
        self.assertEqual(expense.ex_usage_id, first)
        self.assertEqual(self._usages(), first)

    def test_reset_withdraws_the_usage_and_reapproval_makes_one(self):
        """Approuver, remettre en brouillon, corriger, réapprouver : UNE ligne,
        au montant corrigé, et une trace au fil de la dépense."""
        expense, sheet = self._approved(amount=850.0)
        first = expense.ex_usage_id
        # Remise en brouillon faite en fr_CA : la trace suit la langue de qui retire, et la
        # source est anglaise (l'anglais : tests/test_langue_lecteur.py).
        self.env["res.lang"]._activate_lang("fr_CA")
        sheet.with_user(self.expense_user_manager).with_context(
            lang="fr_CA").action_reset_expense_sheets()

        self.assertEqual(expense.state, "reported")
        self.assertFalse(expense.ex_usage_id)
        self.assertFalse(first.exists(), "la ligne d'une approbation retirée se retire aussi")
        self.assertFalse(self._usages())
        self.assertTrue(
            expense.message_ids.filtered(lambda m: "retirée du registre" in (m.body or "")),
            "le retrait laisse une trace au fil de la dépense",
        )

        expense.total_amount_currency = 600.0
        self._submit_and_approve(sheet)
        self.assertEqual(expense.state, "approved")
        self.assertEqual(len(self._usages()), 1)
        self.assertAlmostEqual(expense.ex_usage_id.amount, 600.0)

    def test_employee_resetting_their_own_report_withdraws_the_usage(self):
        """La personne peut remettre SA note en brouillon, sans droit d'écrire
        sur une dépense approuvée : le pont ne doit pas lever pour autant."""
        expense, sheet = self._approved()
        self.assertTrue(expense.ex_usage_id)
        sheet.with_user(self.expense_user_employee).action_reset_expense_sheets()
        self.assertEqual(expense.state, "reported")
        self.assertFalse(self._usages())

    def test_refusing_an_approved_report_withdraws_the_usage(self):
        expense, sheet = self._approved()
        self.assertTrue(expense.ex_usage_id)
        self._refuse(sheet)
        self.assertEqual(expense.state, "refused")
        self.assertFalse(expense.ex_usage_id)
        self.assertFalse(self._usages())

    def test_refusing_a_submitted_report_creates_nothing(self):
        expense = self._expense()
        sheet = self._report(expense)
        sheet.action_submit_sheet()
        self._refuse(sheet)
        self.assertEqual(expense.state, "refused")
        self.assertFalse(self._usages())

    def test_expense_without_benefit_creates_nothing(self):
        other = self.env["product.product"].create({
            "name": "Taxi (essai)", "can_be_expensed": True, "type": "service",
            "property_account_expense_id": self.company_data["default_account_expense"].id,
            "supplier_taxes_id": [Command.clear()],
        })
        expense, _sheet = self._approved(product=other)
        self.assertFalse(expense.ex_benefit_id)
        self.assertFalse(expense.ex_usage_id)

    def test_usage_without_right_is_flagged_not_blocked(self):
        """Le pont ne bloque rien : il signale, comme une saisie manuelle."""
        expense, _sheet = self._approved()
        self.assertTrue(expense.ex_usage_id)
        self.assertFalse(expense.ex_usage_id.entitled)

    def test_usage_is_entitled_when_the_right_is_open(self):
        self.env["bf.ex.entitlement"].create({
            "employee_id": self.expense_employee.id,
            "benefit_id": self.benefit.id,
            "source": "manual",
            "reason": "Pour l'essai.",
        })
        expense, _sheet = self._approved()
        self.assertTrue(expense.ex_usage_id.entitled)

    def test_real_cost_reaches_the_benefit(self):
        """Le but du pont : le coût cesse d'être une estimation."""
        self.env["bf.ex.entitlement"].create({
            "employee_id": self.expense_employee.id, "benefit_id": self.benefit.id,
            "source": "manual", "reason": "Pour l'essai.",
        })
        self._approved(amount=1275.0)
        self.benefit.invalidate_recordset()
        self.assertAlmostEqual(self.benefit.annual_cost, 1275.0)
        self.assertEqual(self.benefit.user_count, 1)
