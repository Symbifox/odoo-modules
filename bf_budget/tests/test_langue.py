"""Les postes proposés par l'assistant prennent la langue de qui les crée.

Leurs noms deviennent ceux des postes créés : écrits hors traduction, ils
sortaient dans la langue de la source pour tout le monde.
"""
from odoo.tests import tagged

from .common import BfBudgetCommon


@tagged("post_install", "-at_install")
class TestLangueDesPostes(BfBudgetCommon):

    def _postes(self, lang):
        self.env["bf.budget.position"].search([("company_id", "=", self.company.id)]).unlink()
        assistant = self.env["bf.budget.position.wizard"].with_context(lang=lang).create(
            {"company_id": self.company.id, "budget_type": "expense"})
        apercu = assistant.preview
        assistant.action_create()
        noms = self.env["bf.budget.position"].search(
            [("company_id", "=", self.company.id), ("code", "=", "OPEX")]).mapped("name")
        return apercu, noms

    def test_les_postes_dans_la_langue_de_qui_les_cree(self):
        Lang = self.env["res.lang"]
        Lang._activate_lang("fr_CA")
        Lang._activate_lang("en_US")
        apercu, noms = self._postes("fr_CA")
        self.assertEqual(noms, ["Charges d'exploitation"])
        self.assertIn("Charges d'exploitation", apercu)
        apercu, noms = self._postes("en_US")
        self.assertEqual(noms, ["Operating expenses"])
        self.assertIn("Operating expenses", apercu)
