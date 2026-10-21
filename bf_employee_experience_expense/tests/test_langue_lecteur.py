"""Les textes du pont des notes de frais dans la langue de leur lecteur.

La source du module est maintenant anglaise ; le français vit dans
`i18n/fr_CA.po`. Deux textes sont écrits en base :

- la note de la ligne d'usage, STOCKÉE au registre de la personne qui a dépensé
  et lue par elle : sa langue (son usager, sinon son contact de travail, sinon la
  société), et non celle de qui approuve (hr_expense.py:122-124, `_ex_reader_lang`
  hr_expense.py:130-149) ;
- la trace du retrait d'une ligne, au fil de la dépense, dans la langue de qui
  retire (hr_expense.py:164-171).

Chaque essai fait agir une langue pour un lecteur de l'autre, par le vrai
parcours d'une note de frais. Les langues sont activées et épinglées sur chaque
usager et chaque appel : selon la base, en_US est absente ou seule.
"""

from odoo import Command, fields
from odoo.tests.common import tagged

from odoo.addons.hr_expense.tests.common import TestExpenseCommon


@tagged("post_install", "-at_install")
class TestLangue(TestExpenseCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.today = fields.Date.context_today(cls.env["hr.employee"])
        cls.company = cls.company_data["company"]
        cls.benefit = cls.env["bf.ex.benefit"].create({
            "name": "Budget de formation (langue)",
            "company_id": cls.company.id,
            "category": "learning",
            "cost_model": "per_use",
        })
        cls.product = cls.env["product.product"].create({
            "name": "Formation (langue)",
            "can_be_expensed": True,
            "type": "service",
            "standard_price": 0.0,
            "property_account_expense_id": cls.company_data["default_account_expense"].id,
            "supplier_taxes_id": [Command.clear()],
        })
        cls.product.product_tmpl_id.ex_benefit_id = cls.benefit

    def _approuvee(self, employe, lang_de_qui_approuve):
        expense = self.env["hr.expense"].create({
            "name": "Cours du soir",
            "employee_id": employe.id,
            "product_id": self.product.id,
            "total_amount_currency": 120.0,
            "tax_ids": [Command.clear()],
            "date": self.today,
            "company_id": self.company.id,
            "currency_id": self.company.currency_id.id,
        })
        action = expense.action_submit_expenses()
        sheet = self.env["hr.expense.sheet"].browse(action["res_id"])
        sheet.action_submit_sheet()
        sheet.with_user(self.expense_user_manager).with_context(
            lang=lang_de_qui_approuve).action_approve_expense_sheets()
        self.assertEqual(expense.state, "approved")
        self.assertTrue(expense.ex_usage_id)
        return expense

    # ── Note de la ligne d'usage (hr_expense.py:122-124) ───────────────
    def test_note_dans_la_langue_de_la_personne_anglaise(self):
        """Approuvée en fr_CA, personne en_CA : « Expense: … »."""
        self.expense_user_employee.lang = "en_CA"
        expense = self._approuvee(self.expense_employee, "fr_CA")
        self.assertEqual(expense.ex_usage_id.note, "Expense: Cours du soir")

    def test_note_dans_la_langue_de_la_personne_francaise(self):
        """Approuvée en en_CA, personne fr_CA : « Note de frais : … »."""
        self.expense_user_employee.lang = "fr_CA"
        expense = self._approuvee(self.expense_employee, "en_CA")
        self.assertEqual(expense.ex_usage_id.note, "Note de frais : Cours du soir")

    def test_note_personne_sans_usager_lit_son_contact_de_travail(self):
        """`_ex_reader_lang` : sans usager, la langue du contact de travail."""
        employe = self.env["hr.employee"].create({
            "name": "Employée sans compte", "company_id": self.company.id,
        })
        self.assertFalse(employe.user_id)
        # La société lit une AUTRE langue : seul le contact de travail donne le français.
        self.company.partner_id.lang = "en_CA"
        employe.work_contact_id.lang = "fr_CA"
        expense = self._approuvee(employe, "en_CA")
        self.assertEqual(expense.ex_usage_id.note, "Note de frais : Cours du soir")

    # ── Trace du retrait (hr_expense.py:164-171) ───────────────────────
    def _trace_du_retrait(self, lang):
        self.expense_user_employee.lang = "en_CA"
        expense = self._approuvee(self.expense_employee, "en_CA")
        expense.with_context(lang=lang)._ex_remove_usage()
        self.assertFalse(expense.ex_usage_id)
        return " ".join(str(m.body) for m in self.env["mail.message"].search([
            ("model", "=", "hr.expense"), ("res_id", "=", expense.id),
        ])).replace("&#34;", '"').replace("&quot;", '"')

    def test_trace_du_retrait_en_francais(self):
        trace = self._trace_du_retrait("fr_CA")
        self.assertIn("Ligne d'usage retirée du registre (« Budget de formation (langue) »",
                      trace.replace("&#39;", "'"))
        self.assertNotIn("Usage line removed", trace)

    def test_trace_du_retrait_en_anglais(self):
        trace = self._trace_du_retrait("en_CA")
        self.assertIn('Usage line removed from the register ("Budget de formation (langue)"', trace)
        self.assertNotIn("retirée du registre", trace)
