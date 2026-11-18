"""La fiche Apps de `bf_mail_company_voice`, lue en anglais au Québec.

Le module n'a aucune chaîne traduisible hors de son manifeste : son seul défaut de langue
était sa fiche dans Apps (`ir.module.module`), que la porte B compte. C'est exactement le
cas d'école.
"""
from odoo.tests import TransactionCase, tagged


@tagged("bf_mail_company_voice", "post_install", "-at_install")
class TestLangue(TransactionCase):

    MODULE = "bf_mail_company_voice"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("en_US", "fr_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_mail_company_voice"], ["fr_CA"], overwrite=True)

    def test_fiche_apps(self):
        module = self.env["ir.module.module"].search(
            [("name", "=", "bf_mail_company_voice")], limit=1)
        self.assertEqual(
            self._mesurer_le_catalogue(module, "shortdesc"),
            "La voix de l'entreprise dans les avis automatiques")
        self.assertEqual(
            module.with_context(lang="en_US").shortdesc,
            "Company Voice for Automatic Notices")
        self.assertIn("avis automatiques parlent",
                      self._mesurer_le_catalogue(module, "summary"))
        self.assertIn("Automatic notices speak",
                      module.with_context(lang="en_US").summary)
    def _mesurer_le_catalogue(self, rec, champ):
        """Retire la valeur fr_CA DÉJÀ en base, puis recharge le catalogue du module.

        🔴 Sans ça, l'essai ne mesure pas le catalogue : `_load_module_terms` ne remplace
        jamais une valeur existante par un `msgstr` vide, et `overwrite=True` n'y change
        rien. L'essai lirait ce qu'une montée précédente a laissé en base, et un
        catalogue amputé — le piège de la liste d'admission du `.pot` — passerait vert.
        Mesuré au premier jeu de mutations.
        """
        rec.update_field_translations(champ, {"fr_CA": False})
        self.env["ir.module.module"]._load_module_terms(
            [self.MODULE], ["fr_CA"], overwrite=True)
        return rec.with_context(lang="fr_CA")[champ]

