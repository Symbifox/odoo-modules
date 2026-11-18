"""La langue du cockpit de Gen.

Le module n'a que des vues. Jusqu'à la 18.0.1.1.0 il n'avait aucun catalogue français, et
son résumé de manifeste était la dernière chaîne française d'une source anglaise.
Le résumé passe à l'anglais, et `i18n/fr_CA.po` porte le français de tout ce que
le module affiche : libellés des vues, filtres, groupements et fiche Apps.
"""
from odoo.tests import TransactionCase, tagged


@tagged("bf_claude_chat_cockpit", "post_install", "-at_install")
class TestLangue(TransactionCase):

    MODULE = "bf_claude_chat_cockpit"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("en_US", "fr_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_claude_chat_cockpit"], ["fr_CA"], overwrite=True)

    def _arch(self, xmlid, lang):
        vue = self.env.ref(xmlid).with_context(lang=lang)
        return vue.arch_db

    def _arch_mesuree(self, xmlid, termes):
        """L'`arch_db` fr_CA d'une vue, après avoir VIDÉ les termes visés et rechargé.

        🔴 `arch_db` est un champ à traduction PAR TERMES : `_load_module_terms` n'en
        retire jamais un terme déjà en base, et `update_field_translations` ignore
        `{"fr_CA": False}` pour ce genre de champ (`models.py` : les valeurs fausses sont
        filtrées avant le traitement). Il faut nommer chaque terme source. Sans ça,
        l'essai lit ce qu'une montée précédente a laissé, et une entrée retirée du `.pot`
        passe inaperçue — mutation survivante au second jeu de mutations.
        """
        vue = self.env.ref(xmlid)
        vue.update_field_translations("arch_db", {"fr_CA": {t: False for t in termes}})
        self.env["ir.module.module"]._load_module_terms(
            [self.MODULE], ["fr_CA"], overwrite=True)
        return vue.with_context(lang="fr_CA").arch_db

    def test_vue_de_recherche_des_sessions(self):
        """Les filtres des sessions existent dans les deux langues, jamais les mêmes."""
        fr = self._arch_mesuree(
            "bf_claude_chat_cockpit.view_claude_session_cockpit_search",
            ["Failing", "Group By"])
        en = self._arch("bf_claude_chat_cockpit.view_claude_session_cockpit_search",
                        "en_US")
        self.assertIn("En échec", fr)
        self.assertIn("Failing", en)
        self.assertNotIn("Failing", fr)
        self.assertNotEqual(fr, en)

    def test_libelles_des_ecrans_de_consommation(self):
        """Les libellés de la consommation se lisent en français par le catalogue,
        et restent anglais pour qui lit en anglais."""
        fr = self._arch_mesuree(
            "bf_claude_chat_cockpit.view_claude_message_search",
            ["With token counts"])
        en = self._arch("bf_claude_chat_cockpit.view_claude_message_search", "en_US")
        self.assertIn("Avec compteurs", fr)
        self.assertIn("With token counts", en)
        self.assertNotIn("Avec compteurs", en)
        liste_fr = self._arch_mesuree(
            "bf_claude_chat_cockpit.view_claude_message_usage_tree",
            ["API-equiv. $", "Turns"])
        liste_en = self._arch(
            "bf_claude_chat_cockpit.view_claude_message_usage_tree", "en_US")
        self.assertIn("Équiv. API $", liste_fr)
        self.assertIn("API-equiv. $", liste_en)

    def test_la_migration_retire_la_cle_fr_CA_de_la_fiche_apps(self):
        """🔴 `base.module_<nom>` est `noupdate` : une clé fr_CA déjà en base GAGNE sur
        le catalogue, et `overwrite=True` n'y change rien — mesuré sur une base d'essai.

        Le `post-migrate` du module la retire juste avant le chargement du catalogue.
        Cet essai le prouve SANS vider la clé lui-même : il en sème une fausse, joue la
        migration, et exige qu'elle ait disparu. C'est le seul essai du lot qui mesure la
        fiche Apps comme la production la vit.
        """
        import importlib.util
        from pathlib import Path as _P
        module = self.env["ir.module.module"].search(
            [("name", "=", self.MODULE)], limit=1)
        self.env.cr.execute(
            "UPDATE ir_module_module SET shortdesc = shortdesc || %s WHERE id = %s",
            ('{"fr_CA": "Valeur périmée à retirer"}', module.id))
        module.invalidate_recordset(["shortdesc"])
        self.assertEqual(module.with_context(lang="fr_CA").shortdesc,
                         "Valeur périmée à retirer", "la clé semée doit bien gagner")
        # `_load_module_terms` ne la retire PAS : c'est tout le piège.
        self.env["ir.module.module"]._load_module_terms(
            [self.MODULE], ["fr_CA"], overwrite=True)
        module.invalidate_recordset(["shortdesc"])
        self.assertEqual(module.with_context(lang="fr_CA").shortdesc,
                         "Valeur périmée à retirer",
                         "le catalogue ne doit PAS pouvoir l'écraser : c'est noupdate")
        # La migration, elle, la retire.
        chemin = next((_P(__file__).resolve().parent.parent / "migrations").glob(
            "*/post-migrate.py"))
        spec = importlib.util.spec_from_file_location("post_migrate_du_lot", chemin)
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)
        mig.migrate(self.env.cr, "18.0.1.0.1")
        module.invalidate_recordset(["shortdesc"])
        self.assertNotEqual(module.with_context(lang="fr_CA").shortdesc,
                            "Valeur périmée à retirer",
                            "le post-migrate doit avoir retiré la clé fr_CA")

    def test_fiche_apps(self):
        """Le résumé du manifeste était la seule chaîne française d'une source anglaise."""
        module = self.env["ir.module.module"].search(
            [("name", "=", "bf_claude_chat_cockpit")], limit=1)
        self.assertEqual(
            module.with_context(lang="en_US").summary,
            "Admin view of Gen sessions: stream failures and token usage")
        self.assertEqual(
            self._mesurer_le_catalogue(module, "summary"),
            "Vue d'administration des sessions Gen : échecs de flux et consommation")
        self.assertEqual(self._mesurer_le_catalogue(module, "shortdesc"), "Gen — Cockpit")
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

