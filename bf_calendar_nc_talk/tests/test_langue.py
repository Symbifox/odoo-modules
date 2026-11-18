"""La langue des refus de Nextcloud Talk.

Trente-sept libellés anglais, aucun catalogue français jusqu'à la 18.0.1.2.0 : un usager
fr_CA lisait tout en anglais, y compris les messages qui lui disent quoi corriger dans ses
paramètres. La source reste anglaise ; `i18n/fr_CA.po` porte le français.

🔴 Aucun essai ne touche le réseau : chaque chemin éprouvé sort avant l'appel OCS.
"""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("bf_calendar_nc_talk", "post_install", "-at_install")
class TestLangue(TransactionCase):

    MODULE = "bf_calendar_nc_talk"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("en_US", "fr_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(["bf_calendar_nc_talk"], ["fr_CA"], overwrite=True)

    def _refus_des_reglages(self, lang):
        reglages = self.env["res.config.settings"].with_context(lang=lang).create({})
        reglages.bf_nc_talk_url = ""
        reglages.bf_nc_talk_user = ""
        reglages.bf_nc_talk_password = ""
        with self.assertRaises(UserError) as pris:
            reglages.action_test_nc_talk_connection()
        return str(pris.exception)

    def test_reglages_incomplets(self):
        fr = self._refus_des_reglages("fr_CA")
        en = self._refus_des_reglages("en_US")
        self.assertIn("Remplissez l'adresse", fr)
        self.assertIn("Fill in URL", en)
        self.assertNotEqual(fr, en)

    def test_evenement_non_enregistre(self):
        """Le refus posé sur l'événement suit aussi la langue."""
        for lang, attendu in (("fr_CA", "Enregistrez l'événement"),
                              ("en_US", "Save the event")):
            evenement = self.env["calendar.event"].with_context(lang=lang).new({
                "name": "Essai de langue",
            })
            with self.assertRaises(UserError) as pris:
                evenement.action_set_nc_talk_videocall_location()
            self.assertIn(attendu, str(pris.exception))

    def test_libelles_des_reglages(self):
        champ = self.env["ir.model.fields"].search([
            ("model", "=", "res.config.settings"), ("name", "=", "bf_nc_talk_user"),
        ], limit=1)
        self.assertEqual(self._mesurer_le_catalogue(champ, "field_description"),
                         "Compte de service")
        self.assertEqual(
            champ.with_context(lang="en_US").field_description, "Service account")

    def test_fiche_apps(self):
        module = self.env["ir.module.module"].search(
            [("name", "=", "bf_calendar_nc_talk")], limit=1)
        # assertEqual sur la chaîne ENTIÈRE : un assertIn sur une sous-chaîne survit au
        # retrait d'un préfixe, donc ne mesure pas la fiche (constat C10).
        self.assertEqual(self._mesurer_le_catalogue(module, "shortdesc"),
                         "Agenda BF — bouton Nextcloud Talk")
        self.assertEqual(module.with_context(lang="en_US").shortdesc,
                         "BF Calendar — Nextcloud Talk button")
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

