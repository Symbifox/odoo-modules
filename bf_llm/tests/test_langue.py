"""La langue des messages de la passerelle LLM.

La source du module est anglaise, mais onze de ses messages d'erreur étaient écrits en
français SANS ACCENTS (« cle », « maniere », « injoignable ») : ni `langue_source.py` ni
`restes.py` ne les voyaient, et un anglophone les lisait en français. Ils sont passés à
l'anglais, et `i18n/fr_CA.po` porte leur français, accentué.
"""
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("bf_llm", "post_install", "-at_install")
class TestLangue(TransactionCase):

    MODULE = "bf_llm"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("en_US", "fr_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(["bf_llm"], ["fr_CA"], overwrite=True)

    def _leve(self, lang, provider="openai_compatible", base_url=False):
        fournisseur = self.env["bf.llm.provider"].with_context(lang=lang).create({
            "name": "Essai de langue",
            "provider": provider,
            "base_url": base_url,
            "model": "modele-essai",
        })
        with self.assertRaises(UserError) as pris:
            fournisseur._effective_base_url()
        return str(pris.exception)

    def test_adresse_de_base_absente(self):
        """Le même refus, dans les deux langues, et jamais le même texte."""
        fr = self._leve("fr_CA")
        en = self._leve("en_US")
        self.assertIn("Aucune adresse de base", fr)
        self.assertIn("No base URL is configured", en)
        self.assertNotEqual(fr, en)

    def test_garde_de_l_adresse_en_clair(self):
        """La contrainte sur l'adresse de base parle aussi la langue de l'usager."""
        for lang, attendu in (("fr_CA", "adresse http(s) valide"),
                              ("en_US", "valid http(s) URL")):
            with self.assertRaises(ValidationError) as pris:
                self.env["bf.llm.provider"].with_context(lang=lang).create({
                    "name": "Adresse tordue", "provider": "openai_compatible",
                    "base_url": "pas-une-adresse", "model": "m",
                })
            self.assertIn(attendu, str(pris.exception))

    def test_libelles_et_aide(self):
        """Les libellés de champ et leur aide viennent du catalogue."""
        champs = self.env["ir.model.fields"].search([("model", "=", "bf.llm.provider")])
        par_nom = {c.name: c for c in champs}
        self.assertEqual(
            self._mesurer_le_catalogue(par_nom["api_key"], "field_description"),
            "Clé API")
        self.assertEqual(
            par_nom["api_key"].with_context(lang="en_US").field_description, "API Key")
        self.assertIn("Surcharge pour la conversation",
                      self._mesurer_le_catalogue(par_nom["model_chat"], "help"))
        self.assertIn(
            "Override for chat",
            par_nom["model_chat"].with_context(lang="en_US").help)
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


    def test_fiche_apps(self):
        module = self.env["ir.module.module"].search([("name", "=", "bf_llm")], limit=1)
        self.assertEqual(self._mesurer_le_catalogue(module, "shortdesc"),
                         "Symbifox — Fournisseur LLM")
        self.assertEqual(module.with_context(lang="en_US").shortdesc,
                         "Symbifox — LLM Provider")
