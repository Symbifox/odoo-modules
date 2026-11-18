"""La langue des libellés de `bf_timezone`.

Douze libellés anglais, aucun catalogue français jusqu'à la 18.0.1.2.0 : un usager fr_CA
lisait « Default timezone » dans ses paramètres. La source reste anglaise, le français vit
dans `i18n/fr_CA.po`.
"""
from odoo.tests import TransactionCase, tagged


@tagged("bf_timezone", "post_install", "-at_install")
class TestLangue(TransactionCase):

    MODULE = "bf_timezone"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("en_US", "fr_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(["bf_timezone"], ["fr_CA"], overwrite=True)

    def test_libelle_du_reglage(self):
        champ = self.env["ir.model.fields"].search([
            ("model", "=", "res.config.settings"), ("name", "=", "bf_default_tz"),
        ], limit=1)
        self.assertTrue(champ, "le champ du réglage doit exister")
        self.assertEqual(self._mesurer_le_catalogue(champ, "field_description"),
                         "Fuseau horaire par défaut")
        self.assertEqual(
            champ.with_context(lang="en_US").field_description, "Default timezone")
    def _mesurer_le_catalogue(self, rec, champ):
        """Retire la valeur fr_CA DÉJÀ en base, puis recharge le catalogue du module.

        🔴 Sans ça, l'essai ne mesure pas le catalogue : `_load_module_terms` ne remplace
        jamais une valeur existante par un `msgstr` vide, donc un catalogue amputé passe
        inaperçu — la base sert ce qu'une montée précédente y a laissé. Mesuré au premier
        jeu de mutations : trois mutations de catalogue survivaient.
        """
        rec.update_field_translations(champ, {"fr_CA": False})
        self.env["ir.module.module"]._load_module_terms(
            [self.MODULE], ["fr_CA"], overwrite=True)
        return rec.with_context(lang="fr_CA")[champ]


    def test_nom_du_modele_outil(self):
        modele = self.env["ir.model"].search([("model", "=", "bf.timezone")], limit=1)
        if not modele:
            self.skipTest("bf.timezone n'est pas un modèle de cette version")
        # Un `assertNotEqual` ne dit pas QUOI est lu : on nomme les deux côtés.
        self.assertEqual(modele.with_context(lang="en_US").name,
                         "Blue Fox Timezone Helpers")
        self.assertEqual(self._mesurer_le_catalogue(modele, "name"),
                         "Outils de fuseau horaire de Blue Fox")

    def test_fiche_apps(self):
        module = self.env["ir.module.module"].search(
            [("name", "=", "bf_timezone")], limit=1)
        # La marque est celle de CET arbre : le manifeste la porte, le catalogue
        # la suit. assertEqual sur la chaîne entière.
        anglais = module.with_context(lang="en_US").shortdesc
        marque = "Symbifox" if anglais.startswith("Symbifox") else "Blue Fox"
        self.assertEqual(anglais, f"{marque} Timezone Utilities")
        self.assertEqual(self._mesurer_le_catalogue(module, "shortdesc"),
                         f"Fuseaux horaires {marque}")
        self.assertIn("fuseau par défaut", self._mesurer_le_catalogue(module, "summary"))
