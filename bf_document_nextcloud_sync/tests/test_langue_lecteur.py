"""Ce que la synchro Nextcloud écrit au document se lit dans la langue de son lecteur
.

La source du module passe du français à l'anglais; le français vit dans
`i18n/fr_CA.po`. Deux défauts sont corrigés ici :
- les notes au fil (partage créé, version téléversée) étaient des `str` : le fil
  échappait leurs balises, qui s'affichaient en clair;
- le travail planifié qui détecte un fichier modifié écrivait journal de version,
  activité et état de la configuration en français, sans langue à lui : ils suivent
  maintenant le responsable du document (ou son auteur), sinon la société.

Aucun réseau : les appels WebDAV et OCS de la configuration sont simulés.
Les essais activent fr_CA et en_CA et épinglent chaque langue.
"""

import base64
import importlib.util
import json
from unittest.mock import patch

from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLangue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_document_nextcloud_sync"], ["fr_CA"], overwrite=True)
        cls.config = cls.env["nextcloud.document.config"].create({
            "name": "Essai langue", "nextcloud_base_url": "https://nc.example.test",
            "webdav_path": "/remote.php/dav/files/", "nextcloud_user": "service",
        })
        cls.type_doc = cls.env["project.document.type"].create({"name": "Politique", "code": "PLT9"})
        users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.anglo = users.create({"name": "Ann Essai", "login": "ann@langue.invalid",
                                  "email": "ann@langue.invalid", "lang": "en_CA"})
        cls.franco = users.create({"name": "Félix Essai", "login": "felix@langue.invalid",
                                   "email": "felix@langue.invalid", "lang": "fr_CA"})
        cls.Config = type(cls.config)

    def _doc(self, code, owner=None, author=None):
        return self.env["project.document"].create({
            "name": "Politique " + code, "code": code, "type_id": self.type_doc.id,
            "nc_config_id": self.config.id, "nc_file_path": f"/Politiques/{code}.odt",
            "nc_etag": "etag-avant", "owner_id": owner.id if owner else False,
            "author_id": author.id if author else False,
        })

    # ------------------------------------------------------------------
    # Taille affichée (champ non stocké) : unités dans la langue de qui lit
    # ------------------------------------------------------------------
    def test_taille_affichee_dans_la_langue_de_qui_lit(self):
        """`_compute_nc_file_size_display` écrivait « Ko » en dur : un anglophone lit « KB »."""
        doc = self._doc("TAI-9")
        doc.nc_file_size = 1536
        self.assertEqual(doc.with_context(lang="en_CA").nc_file_size_display, "1.5 KB")
        self.assertEqual(doc.with_context(lang="fr_CA").nc_file_size_display, "1.5 Ko")
        doc.nc_file_size = 512
        self.assertEqual(doc.with_context(lang="en_CA").nc_file_size_display, "512 B")
        self.assertEqual(doc.with_context(lang="fr_CA").nc_file_size_display, "512 o")

    # ------------------------------------------------------------------
    # Notes au fil en Markup
    # ------------------------------------------------------------------
    def _televerser(self, lang):
        doc = self._doc("TEL-" + lang)
        wizard = self.env["document.nc.upload.wizard"].with_context(lang=lang).create({
            "document_id": doc.id, "nc_config_id": self.config.id,
            "nc_file_path": "/Politiques/R&D.pdf", "file_name": "R&D.pdf",
            "file_data": base64.b64encode(b"%PDF-1.4 essai"), "create_version_record": False,
        })
        with patch.object(self.Config, "_webdav_mkcol"), patch.object(self.Config, "_webdav_put"), \
                patch.object(self.Config, "_webdav_propfind", return_value=[]):
            wizard.action_upload()
        notes = doc.message_ids.filtered(lambda m: m.message_type == "comment")
        self.assertEqual(len(notes), 1)
        return str(notes.body)

    def test_televersement_note_en_html_dans_la_langue_de_qui_televerse(self):
        """document_nc_upload_wizard.py:181 : balises rendues, nom de fichier échappé."""
        fr = self._televerser("fr_CA")
        self.assertIn("<strong>Nouvelle version televersee sur Nextcloud</strong>", fr)
        self.assertIn("R&amp;D.pdf", fr)
        self.assertNotIn("&lt;strong&gt;", fr)
        en = self._televerser("en_CA")
        self.assertIn("<strong>New version uploaded to Nextcloud</strong>", en)
        self.assertIn("<p>Size: 14 bytes</p>", en)
        self.assertNotIn("&lt;p&gt;", en)

    def _partager(self, lang):
        doc = self._doc("PAR-" + lang)
        info = {"url": "https://nc.example.test/s/abc&x=1", "password": "secret-9", "expire_date": None}
        with patch.object(self.Config, "_ocs_create_share", return_value=info):
            doc.with_context(lang=lang).action_create_share_link()
        notes = doc.message_ids.filtered(lambda m: m.message_type == "comment")
        self.assertEqual(len(notes), 1)
        return str(notes.body)

    def test_partage_note_en_html_dans_la_langue_de_qui_partage(self):
        """project_document.py:280 : balises rendues, adresse échappée, mot de passe absent."""
        fr = self._partager("fr_CA")
        self.assertIn("<strong>Lien de partage Nextcloud créé</strong>", fr)
        self.assertIn('href="https://nc.example.test/s/abc&amp;x=1"', fr)
        self.assertIn("Expiration : s.o.", fr)
        self.assertNotIn("&lt;strong&gt;", fr)
        self.assertNotIn("secret-9", fr)
        en = self._partager("en_CA")
        self.assertIn("<strong>Nextcloud share link created</strong>", en)
        self.assertIn("<p>Expiry: N/A</p>", en)
        self.assertNotIn("secret-9", en)

    # ------------------------------------------------------------------
    # Travail planifié : journal de version, activité, last_sync_message
    # ------------------------------------------------------------------
    def _verifier(self, docs, lang_du_travail):
        chemins = {d.nc_file_path for d in docs}

        def propfind(config, path, depth="1"):
            if path in chemins:
                return [{"etag": "etag-apres", "size": 2048,
                         "last_modified": "Mon, 19 Oct 2026 12:00:00 GMT"}]
            return []

        with patch.object(self.Config, "_webdav_propfind", autospec=True, side_effect=propfind):
            self.env["project.document"].with_context(lang=lang_du_travail)._cron_check_nc_modifications()

    def _version(self, doc):
        return self.env["project.document.version"].search([("document_id", "=", doc.id)])

    def _activite(self, doc):
        return self.env["mail.activity"].search([("res_model", "=", "project.document"), ("res_id", "=", doc.id)])

    def test_travail_planifie_responsable_anglophone(self):
        """project_document.py:359,376 et 505-515 : le responsable anglophone lit l'anglais,
        même quand le travail tourne en français."""
        doc = self._doc("CRA-1", owner=self.anglo, author=self.franco)
        self._verifier(doc, "fr_CA")
        version = self._version(doc)
        self.assertEqual(version.change_summary, "Change detected on Nextcloud")
        self.assertIn("Change detected automatically on Nextcloud.", version.changelog)
        self.assertIn("- Modified on: 2026-10-19 12:00:00", version.changelog)
        activite = self._activite(doc)
        self.assertEqual(activite.summary, "File changed on Nextcloud: Politique CRA-1")
        self.assertIn("was changed on Nextcloud since the last check", str(activite.note))

    def test_travail_planifie_responsable_francophone(self):
        doc = self._doc("CRF-1", owner=self.franco, author=self.anglo)
        self._verifier(doc, "en_US")
        version = self._version(doc)
        self.assertEqual(version.change_summary, "Modification detectee sur Nextcloud")
        self.assertIn("Modification détectée automatiquement sur Nextcloud.", version.changelog)
        self.assertIn("- Modifié le : 2026-10-19 12:00:00", version.changelog)
        activite = self._activite(doc)
        self.assertEqual(activite.summary, "Fichier modifie sur Nextcloud: Politique CRF-1")
        self.assertIn("a ete modifie sur Nextcloud", str(activite.note))

    def test_travail_planifie_sans_responsable_l_auteur(self):
        """_nc_reader_lang : sans responsable, l'auteur."""
        doc = self._doc("CRA-2", author=self.anglo)
        self._verifier(doc, "fr_CA")
        self.assertEqual(self._version(doc).change_summary, "Change detected on Nextcloud")
        self.assertEqual(self._activite(doc).summary, "File changed on Nextcloud: Politique CRA-2")

    def test_travail_planifie_taille_dans_la_langue_du_responsable(self):
        doc = self._doc("CRT-1", owner=self.franco)
        doc.nc_file_size = 1024
        self._verifier(doc, "en_US")
        self.assertIn("- Taille : 1024 -> 2048 octets (+1024)", self._version(doc).changelog)

    def test_travail_planifie_sans_personne_langue_de_la_societe(self):
        """_nc_reader_lang et last_sync_message (project_document.py:543) : la société."""
        doc = self._doc("CRS-1")
        self.assertFalse(doc.owner_id or doc.author_id)
        doc.company_id.partner_id.lang = "en_CA"
        self.config.company_id.partner_id.lang = "en_CA"
        self._verifier(doc, "fr_CA")
        self.assertEqual(self._version(doc).change_summary, "Change detected on Nextcloud")
        self.assertEqual(self.config.last_sync_message, "Check: 1 changed out of 1 documents")
        self.assertEqual(self.config.last_sync_status, "success")

    def test_etat_de_la_configuration_societe_francophone(self):
        doc = self._doc("CRS-2", owner=self.anglo)
        self.config.company_id.partner_id.lang = "fr_CA"
        self._verifier(doc, "en_US")
        self.assertEqual(self.config.last_sync_message, "Verification: 1 modifie(s) sur 1 documents")

    # ------------------------------------------------------------------
    # Langue désactivée gardée par la société (_nc_installed_lang)
    # ------------------------------------------------------------------
    def _societe_en_langue_inactive(self, doc):
        """La fiche de la société garde le code d'une langue désactivée depuis (de_DE).

        L'ORM refuse d'écrire une langue non installée : on la pose en SQL, comme
        une désactivation faite après coup l'aurait laissée.
        """
        installees = [code for code, _nom in self.env["res.lang"].get_installed()]
        if "de_DE" in installees:
            self.skipTest("de_DE est active sur cette base")
        partenaires = (doc.company_id.partner_id | self.config.company_id.partner_id).ids
        self.env.cr.execute("UPDATE res_partner SET lang = 'de_DE' WHERE id IN %s", (tuple(partenaires),))
        self.env.invalidate_all()
        self.assertEqual(self.config.company_id.partner_id.lang, "de_DE")

    def test_langue_inactive_de_la_societe_repli_sur_la_langue_du_travail(self):
        """project_document.py:362 (_nc_reader_lang) et :543 (last_sync_message) : une langue
        inactive levait « Invalid language code » une fois l'ETag écrit (version perdue)."""
        doc = self._doc("CRI-1")
        self._societe_en_langue_inactive(doc)
        self._verifier(doc, "fr_CA")
        self.assertEqual(doc.nc_etag, "etag-apres")
        self.assertEqual(self._version(doc).change_summary, "Modification detectee sur Nextcloud")
        self.assertEqual(self.config.last_sync_status, "success")
        self.assertEqual(self.config.last_sync_message, "Verification: 1 modifie(s) sur 1 documents")

    def test_langue_inactive_sans_langue_au_travail_repli_installe(self):
        """Sans langue au contexte : une langue installée (en_US sur la base de BF), pas d'erreur."""
        doc = self._doc("CRI-2")
        self._societe_en_langue_inactive(doc)
        self._verifier(doc, None)
        installees = [code for code, _nom in self.env["res.lang"].get_installed()]
        attendu = "en_US" if "en_US" in installees else installees[0]
        self.assertEqual(self.env["project.document"]._nc_reader_lang(doc), attendu)
        self.assertEqual(self._version(doc).change_summary,
                         self.env["project.document"].with_context(lang=attendu).env._("Change detected on Nextcloud"))
        self.assertEqual(self.config.last_sync_status, "success")

    # ------------------------------------------------------------------
    # Migration 18.0.1.6.0 : travail planifié et étapes d'intégration (noupdate)
    # ------------------------------------------------------------------
    CAS = (
        # (xmlid, champ, français livré, anglais du fichier de données)
        ("ir_cron_check_nc_modifications", "name", "Documents: Verifier modifications Nextcloud",
         "Documents: check Nextcloud changes"),
        ("bf_onb_step_settings", "title", "Connecter Nextcloud", "Connect Nextcloud"),
        ("bf_onb_step_settings", "button_text", "Ouvrir Paramètres", "Open Settings"),
        ("bf_onb_step_config", "title", "Configurer le mapping de dossiers", "Set up the folder mapping"),
        ("bf_onboarding_panel", "name", "Sync Nextcloud — Documents", "Nextcloud sync: documents"),
    )

    def _porteur(self, xmlid):
        rec = self.env.ref("bf_document_nextcloud_sync." + xmlid)
        return rec.ir_actions_server_id if rec._name == "ir.cron" else rec

    def _lire(self, xmlid, champ, lang):
        rec = self._porteur(xmlid)
        self.env.cr.execute(f'SELECT "{champ}"->>%s FROM "{rec._table}" WHERE id = %s', (lang, rec.id))
        return self.env.cr.fetchone()[0]

    def _poser(self, xmlid, champ, valeur):
        rec = self._porteur(xmlid)
        self.env.cr.execute(f'UPDATE "{rec._table}" SET "{champ}" = %s::jsonb WHERE id = %s',
                            (json.dumps(valeur), rec.id))
        self.env.invalidate_all()

    def _monter(self):
        for etape in ("post", "end"):
            chemin = get_module_path("bf_document_nextcloud_sync") + f"/migrations/18.0.1.6.0/{etape}-migrate.py"
            spec = importlib.util.spec_from_file_location(f"bf_doc_sync_mig_{etape}", chemin)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.migrate(self.env.cr, "18.0.1.5.0")
        self.env.invalidate_all()

    def test_migration_18_0_1_6_0_travail_planifie_et_integration_livres(self):
        """Le français livré en en_US passe à l'anglais; le français va en fr_CA."""
        for xmlid, champ, fr, _en in self.CAS:
            self._poser(xmlid, champ, {"en_US": fr})
        self._monter()
        for xmlid, champ, fr, en in self.CAS:
            self.assertEqual(self._lire(xmlid, champ, "en_US"), en, (xmlid, champ))
            self.assertEqual(self._lire(xmlid, champ, "fr_CA"), fr, (xmlid, champ))

    def test_migration_18_0_1_6_0_retouche_gardee(self):
        """Un travail planifié renommé à la main garde son nom, en_US comme fr_CA."""
        self._poser("ir_cron_check_nc_modifications", "name", {"en_US": "Veille des politiques"})
        self._monter()
        self.assertEqual(self._lire("ir_cron_check_nc_modifications", "name", "en_US"), "Veille des politiques")
        self.assertEqual(self._lire("ir_cron_check_nc_modifications", "name", "fr_CA"), "Veille des politiques")
