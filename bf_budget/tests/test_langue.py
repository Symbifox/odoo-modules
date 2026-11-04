"""Les postes proposés par l'assistant prennent la langue de qui les crée.

Leurs noms deviennent ceux des postes créés : écrits hors traduction, ils
sortaient dans la langue de la source pour tout le monde.
"""
from odoo.exceptions import UserError
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


@tagged("post_install", "-at_install")
class TestLangueDesGardes(BfBudgetCommon):
    """🔴 Les messages des gardes doivent exister dans le catalogue.

    Trois messages ajoutés par le gel du plan (18.0.1.2.1) n'étaient ni au
    `.pot` ni au `fr_CA.po` : ils sortaient en anglais sur une base française,
    sur la seule surface du lot qu'un usager voit. Rien ne le signalait, parce
    qu'aucun essai ne regardait la langue d'un refus. Celui-ci le fait.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.budget = cls._make_budget(planned=1200.0, state="open")

    def _refus(self, geste):
        with self.assertRaises(UserError) as capture:
            geste()
        return str(capture.exception)

    def test_les_refus_du_gel_parlent_francais(self):
        ligne = self.budget.line_ids.with_context(lang="fr_CA")
        self.assertIn(
            "n'est plus au brouillon",
            self._refus(lambda: ligne.write({"amount_planned": 1200.0})),
        )
        self.assertIn(
            "brouillon",
            self._refus(ligne.action_spread_evenly),
        )
        self.assertIn(
            "répartition mensuelle",
            self._refus(
                lambda: ligne.period_ids[0].with_context(
                    lang="fr_CA").write({"amount_planned": 1.0})
            ),
        )

    def test_les_refus_du_gel_complet_parlent_francais(self):
        """Les gardes du lot « gel » (18.0.1.3.0), même règle : un refus
        qu'un usager voit sort dans sa langue."""
        ligne = self.budget.line_ids.with_context(lang="fr_CA")
        self.assertIn("aucune ligne ne peut s'y", self._refus(ligne.copy))
        self.assertIn(
            "ne se modifie plus",
            self._refus(lambda: ligne.period_ids.sorted("date_start")[0]
                        .with_context(lang="fr_CA").unlink()),
        )
        self.assertIn("ne se supprime plus", self._refus(ligne.unlink))

    def test_le_refus_du_poste_etranger_parle_francais(self):
        """La contrainte de société est une ValidationError, pas une
        UserError : elle a son propre chemin de langue, à éprouver aussi."""
        from odoo.exceptions import ValidationError

        autre = self.env["res.company"].create({"name": "Société langue"})
        compte = self.env["account.account"].with_company(autre).create({
            "code": "609997",
            "name": "Charges langue",
            "account_type": "expense",
        })
        poste = self.env["bf.budget.position"].create({
            "name": "Poste langue", "code": "LNG", "budget_type": "expense",
            "company_id": autre.id, "account_ids": [(6, 0, compte.ids)],
        })
        # ⚠️ Sur un budget OUVERT, c'est la garde du PLAN qui parlerait avant
        # la contrainte : le chemin de la contrainte passe par un brouillon.
        brouillon = self._make_budget(planned=300.0, state="draft")
        ligne = brouillon.line_ids.with_context(lang="fr_CA")
        with self.assertRaises(ValidationError) as capture:
            ligne.write({"position_id": poste.id})
        self.assertIn("autre société", str(capture.exception))

    def test_les_memes_refus_lus_en_anglais_restent_la_source(self):
        ligne = self.budget.line_ids.with_context(lang="en_US")
        self.assertIn(
            "no longer a draft",
            self._refus(lambda: ligne.write({"amount_planned": 1200.0})),
        )
