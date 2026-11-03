"""La section Budget parle la langue de qui la lit.

Le digest est envoyé par une tâche planifiée, qui ne porte aucune langue : le
socle pose donc celle du destinataire avant de rendre le courriel. Les libellés
de cette section sont écrits en anglais dans la source et traduits par
`i18n/fr_CA.po` ; les montants et les pourcentages suivent la même langue, sans
quoi un courriel dirait « Planned » au-dessus de « 1 200,00 $ ».
"""
from odoo.tests.common import tagged

from odoo.addons.bf_budget.tests.common import BfBudgetCommon


@tagged("post_install", "-at_install")
class TestLangueDeLaSection(BfBudgetCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Lang = cls.env["res.lang"]
        # ⚠️ C'est `_activate_lang` qui charge le fr_CA.po des modules déjà
        # installés : sans elle, la base n'a aucune traduction et l'essai
        # mesurerait l'anglais contre l'anglais.
        Lang._activate_lang("fr_CA")
        Lang._activate_lang("en_US")
        # Voir le décor de test_digest_section : une VRAIE personne, qui peut
        # lire le budget, et dont la langue est celle qu'on mesure.
        cls.responsable = cls.env["res.users"].create({
            "name": "Lectrice du digest", "login": "langue_budget_digest",
            "company_id": cls.company.id, "company_ids": [(6, 0, [cls.company.id])],
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("bf_budget.group_bf_budget_user").id,
            ])],
        })
        cls.config = cls.env["daily.digest.config"].create({
            "name": "Digest d'essai langue", "company_id": cls.company.id,
        })
        cls.budget = cls._make_budget(planned=1200.0, state="open")
        cls.budget.user_id = cls.responsable
        cls._post_bill(cls.account_software, 12000.0)

    def _section(self, lang):
        self.responsable.lang = lang
        self.env.invalidate_all()
        return self.config.with_context(lang=lang)._render_budget_section(self.responsable)

    def test_en_francais(self):
        html = self._section("fr_CA")
        self.assertIn("en alerte", html)
        self.assertIn("Prévu", html)
        self.assertIn("Engagé", html)
        self.assertIn("Théorique", html)
        self.assertIn("Écart", html)
        # Le séparateur décimal de fr_CA, qui prouve que les montants ont suivi
        # la même langue que les en-têtes.
        self.assertIn("1 200,00", html.replace(" ", " ").replace("\xa0", " "))

    def test_en_anglais_la_source(self):
        html = self._section("en_US")
        self.assertIn("flagged line(s)", html)
        self.assertIn("Planned", html)
        self.assertIn("Committed", html)
        self.assertNotIn("en alerte", html)
        self.assertIn("1,200.00", html.replace(" ", " ").replace("\xa0", " "))
