"""Le navigateur Nextcloud parle la langue de qui le lit.

La source du module passe du français à l'anglais; le français vit dans
`i18n/fr_CA.po`. Le code écrivait en dur, en français, ce qu'il affiche (type de
partage, unités de taille, miette de la racine) et ce qu'il range en base (note du
dossier client, noms des préréglages, nom de la connexion en attente) : un usager
réglé en anglais le lisait en français. Chaque essai épingle la langue du lecteur
et celle de l'appel, l'une contre l'autre.

Aucun réseau : `requests` est simulé et la clé Fernet aussi, comme dans
test_nc_links et test_nc_client_folder_lot2. Les essais activent fr_CA et en_CA.
"""

import base64
import importlib.util
from unittest.mock import MagicMock, patch
from urllib.parse import quote

from cryptography.fernet import Fernet

from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_nextcloud_browser.models.bf_nc_browser import SHARE_KINDS, _human_size

KEY = Fernet.generate_key()
BASE = "https://nc.example.test"
PREFIX = "/Equipe/"
CLIENTS = "/Equipe/(Clients)"
TEMPLATE = CLIENTS + "/(DOSSIER TYPE)"
DAV = "/remote.php/dav/files/alice@corp"


def _response(status, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.json.return_value = json_data if json_data is not None else {}
    return resp


def _listing(folder, dirs=(), files=()):
    """PROPFIND profondeur 1 de `folder` : lui-même, ses dossiers, ses fichiers (nom, taille)."""
    items = [(folder, True, 0)] + [(folder + "/" + d, True, 0) for d in dirs] + [
        (folder + "/" + f, False, size) for f, size in files]
    parts = []
    for i, (path, is_dir, size) in enumerate(items):
        href = quote(DAV + path + ("/" if is_dir else ""))
        rtype = "<d:collection/>" if is_dir else ""
        parts.append(
            "<d:response><d:href>%s</d:href><d:propstat><d:prop>"
            "<d:resourcetype>%s</d:resourcetype><oc:fileid>%d</oc:fileid><oc:size>%d</oc:size>"
            "<d:getlastmodified>Mon, 14 Sep 2026 12:00:00 GMT</d:getlastmodified>"
            "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
            % (href, rtype, 700 + i, size)
        )
    return ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
            + "".join(parts) + "</d:multistatus>")


def _share(share_type, **extra):
    return {
        "id": "5", "share_type": share_type, "token": "tok5555555555",
        "url": BASE + "/s/tok5555555555", "path": "/Equipe/a.pdf", "share_with": "bob",
        "item_type": "file", "permissions": 1, "expiration": None, "password": None,
        "uid_owner": "alice@corp", "uid_file_owner": "alice@corp", "can_delete": True,
        **extra,
    }


@tagged("post_install", "-at_install")
class TestLangue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_document_nextcloud_sync", "bf_nextcloud_browser"], ["fr_CA"], overwrite=True)
        Config = cls.env["nextcloud.document.config"]
        cls._key_patch = patch.object(type(Config), "_get_encryption_key", return_value=KEY)
        cls._key_patch.start()
        cls.addClassCleanup(cls._key_patch.stop)
        cls.config = Config.create({
            "name": "Essai langue",
            "nextcloud_base_url": BASE,
            "webdav_path": "/remote.php/dav/files/",
            "nextcloud_user": "service",
            "nextcloud_app_password": "service-secret",
            "browser_root_prefix": PREFIX,
            "nc_clients_root": CLIENTS + "/",
            "nc_client_template": TEMPLATE,
        })
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_document_nextcloud_sync.default_config_id", cls.config.id
        )
        group = cls.env.ref("bf_nextcloud_browser.group_nc_browser_user")
        groups = [cls.env.ref("base.group_user").id, cls.env.ref("project.group_project_manager").id,
                  cls.env.ref("base.group_partner_manager").id, group.id]
        cls.alice = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Alice", "login": "alice@langue.invalid", "email": "alice@langue.invalid",
            "lang": "en_CA", "groups_id": [(6, 0, groups)],
        })
        cls.env["bf.nc.user.credential"].sudo().create({
            "user_id": cls.alice.id, "config_id": cls.config.id, "state": "connected",
            "nc_login": "alice@corp", "nc_user_id": "alice@corp", "nc_display_name": "Alice",
            "nc_server": BASE, "app_password_encrypted": cls.config._encrypt_value("pw"),
        })
        cls.valberge = cls.env["res.partner"].create({"name": "Toitures Valberge inc.", "is_company": True})

    def _browser(self, lang):
        return self.env["bf.nc.browser"].with_user(self.alice).with_context(lang=lang)

    def _env(self, lang):
        return self.env(context=dict(self.env.context, lang=lang))

    # ------------------------------------------------------------------
    # SHARE_KINDS : le type d'un partage (bf_nc_browser.py:177, lu à 927-928)
    # ------------------------------------------------------------------
    def test_share_kinds_type_de_partage_dans_la_langue_de_l_ecran(self):
        """« Lien public » en fr_CA, « Public link » en anglais; « Autre » pour un type inconnu.

        Le libellé est lu hors de l'appel `env._()` : écrit dedans, l'extracteur prenait
        `share["share_type"]` pour un texte et le catalogue n'avait rien à donner.
        """
        fr = self._browser("fr_CA")._share_view(_share(3), PREFIX)
        en = self._browser("en_CA")._share_view(_share(3), PREFIX)
        self.assertEqual(fr["kind"], "Lien public")
        self.assertEqual(en["kind"], "Public link")
        self.assertEqual(self._browser("fr_CA")._share_view(_share(0), PREFIX)["kind"], "Personne")
        self.assertEqual(self._browser("en_CA")._share_view(_share(0), PREFIX)["kind"], "Person")
        self.assertEqual(self._browser("fr_CA")._share_view(_share(99), PREFIX)["kind"], "Autre")
        self.assertEqual(self._browser("en_CA")._share_view(_share(99), PREFIX)["kind"], "Other")

    def test_share_kinds_chaque_type_a_son_francais(self):
        """Aucun type de partage ne reste en anglais à l'écran d'un francophone."""
        for share_type in SHARE_KINDS:
            fr = self._browser("fr_CA")._share_view(_share(share_type), PREFIX)["kind"]
            en = self._browser("en_CA")._share_view(_share(share_type), PREFIX)["kind"]
            self.assertNotEqual(fr, en, share_type)

    # ------------------------------------------------------------------
    # _human_size(num, env) : unités (bf_nc_browser.py:189-202)
    # ------------------------------------------------------------------
    def test_human_size_unites_dans_la_langue_de_l_environnement(self):
        """Go, Mo, Ko, o en fr_CA; GB, MB, KB, B en anglais (en_US comme en_CA)."""
        fr, en, us = self._env("fr_CA"), self._env("en_CA"), self._env("en_US")
        self.assertEqual(_human_size(500, fr), "500 o")
        self.assertEqual(_human_size(500, en), "500 B")
        self.assertEqual(_human_size(1536, fr), "1.5 Ko")
        self.assertEqual(_human_size(1536, en), "1.5 KB")
        self.assertEqual(_human_size(3 * 1024 ** 2, fr), "3.0 Mo")
        self.assertEqual(_human_size(3 * 1024 ** 2, us), "3.0 MB")
        self.assertEqual(_human_size(5 * 1024 ** 3, fr), "5.0 Go")
        self.assertEqual(_human_size(5 * 1024 ** 3, us), "5.0 GB")
        self.assertEqual(_human_size(2 * 1024 ** 4, fr), "2.0 To")
        # Au-delà du To, l'unité de repli (le nombre y garde un défaut d'origine : il n'est
        # pas divisé une fois de plus, 2 Pio s'écrivent « 2048.0 Po »; signalé, pas corrigé).
        self.assertTrue(_human_size(2 * 1024 ** 5, fr).endswith(" Po"))
        self.assertTrue(_human_size(2 * 1024 ** 5, en).endswith(" PB"))

    def test_human_size_liste_du_dossier_et_miette_root_folder(self):
        """La liste d'un dossier : taille (bf_nc_browser.py:565) et miette « Root folder » (589)."""
        listing = _listing("/Equipe", dirs=("Clients",), files=(("devis.pdf", 1536),))

        def route(method, url, **kwargs):
            if method == "PROPFIND":
                return _response(207, text=listing)
            return _response(200, {"ocs": {"meta": {"status": "ok"}, "data": []}})

        for lang, racine, taille in (("fr_CA", "Racine", "1.5 Ko"), ("en_CA", "Root folder", "1.5 KB")):
            with patch("requests.request", side_effect=route):
                res = self._browser(lang).root_browse_dir("")
            self.assertEqual(res["breadcrumb"][0]["name"], racine, lang)
            fichier = [e for e in res["entries"] if e["name"] == "devis.pdf"]
            self.assertEqual(fichier[0]["size_display"], taille, lang)

    def test_human_size_pieces_du_fil(self):
        """Les pièces jointes qu'on peut classer : leur taille (bf_nc_browser.py:1186)."""
        task = self.env["project.task"].create({"name": "Tâche taille", "project_id": self.env[
            "project.project"].create({"name": "Projet taille"}).id})
        self.env["ir.attachment"].create({
            "name": "plan.pdf", "datas": base64.b64encode(b"x" * 2048),
            "res_model": "project.task", "res_id": task.id,
        })
        fr = self._browser("fr_CA").thread_attachments("project.task", task.id)["attachments"]
        en = self._browser("en_CA").thread_attachments("project.task", task.id)["attachments"]
        self.assertEqual([a["size_display"] for a in fr], ["2.0 Ko"])
        self.assertEqual([a["size_display"] for a in en], ["2.0 KB"])

    # ------------------------------------------------------------------
    # « Nextcloud file » : nom d'un lien sans nom de fichier (bf_nc_browser.py:791)
    # ------------------------------------------------------------------
    def test_nextcloud_file_nom_du_lien_d_un_element_de_matrice(self):
        """Un lien vers la racine n'a pas de nom de fichier : « Fichier Nextcloud » en fr_CA."""
        projet = self.env["project.project"].create({"name": "Projet matrice (essai)"})
        matrice = self.env["project.knowledge.matrix"].create({"name": "Matrice", "project_id": projet.id})
        section = self.env["project.knowledge.section"].create({"name": "Politiques", "code": "S9"})
        Browser = type(self.env["bf.nc.browser"])
        lien = {"ok": True, "kind": "internal", "name": "", "url": BASE + "/f/1"}
        for lang, attendu, decision in (("fr_CA", "Fichier Nextcloud", "LF91"), ("en_CA", "Nextcloud file", "LF92")):
            item = self.env["project.knowledge.item"].create({
                "name": "Élément " + lang, "matrix_id": matrice.id, "section_id": section.id,
                "decision_id": decision, "project_id": projet.id,
            })
            with patch.object(Browser, "_resolve_path", return_value=(self.config, "/", "/")), \
                    patch.object(Browser, "_make_link", return_value=dict(lien)):
                self.env["bf.nc.browser"].with_context(lang=lang).link_to_knowledge_item(
                    "project.project", projet.id, "", item.id, mode="internal")
            self.assertEqual(item.attachment_ids.mapped("name"), [attendu], lang)

    # ------------------------------------------------------------------
    # Note « dossier client créé » (res_partner.py:189-192)
    # ------------------------------------------------------------------
    def _creer_dossier(self, user, **context):
        listing = _listing(CLIENTS, dirs=("(DOSSIER TYPE)", "029 - ZOLTA"))

        def route(method, url, **kwargs):
            if method == "PROPFIND":
                return _response(207, text=listing)
            if method == "COPY":
                return _response(201)
            return _response(500)

        with patch("requests.request", side_effect=route):
            path = self.valberge.with_user(user).with_context(**context).nc_create_client_folder()
        notes = self.valberge.message_ids.filtered(lambda m: path in (m.body or ""))
        self.assertEqual(len(notes), 1)
        return str(notes.body)

    def test_note_dossier_client_dans_la_langue_de_l_auteur_sans_langue_au_contexte(self):
        """Appelée par RPC sans langue (script d'accueil) : la langue de l'auteur, pas l'anglais."""
        self.assertNotIn("lang", self.env.context)
        self.alice.lang = "fr_CA"
        body = self._creer_dossier(self.alice)
        self.assertIn("Dossier client créé dans Nextcloud :", body)
        self.assertNotIn("Client folder created", body)

    def test_note_dossier_client_auteur_anglophone(self):
        body = self._creer_dossier(self.alice)
        self.assertIn("Client folder created in Nextcloud: " + CLIENTS + "/030 - Toitures Valberge inc", body)
        self.assertNotIn("Dossier client", body)

    def test_note_dossier_client_la_langue_de_l_ecran_prime(self):
        """Depuis le formulaire, le contexte porte la langue de l'écran : elle prime."""
        self.alice.lang = "fr_CA"
        body = self._creer_dossier(self.alice, lang="en_CA")
        self.assertIn("Client folder created in Nextcloud:", body)

    # ------------------------------------------------------------------
    # Préréglages Internal / External (nextcloud_document_config.py:418-444)
    # ------------------------------------------------------------------
    def _prereglages(self, langue_societe, langue_appel):
        self.config.company_id.partner_id.lang = langue_societe
        self.config.share_preset_ids.unlink()
        self.config.with_context(lang=langue_appel)._ensure_share_presets()
        return self.config.share_preset_ids.sorted("sequence").mapped("name")

    def test_prereglages_nommes_dans_la_langue_de_la_societe_francaise(self):
        """Nom rangé, partagé par tous : la langue de la société, pas celle du premier qui ouvre."""
        self.assertEqual(self._prereglages("fr_CA", "en_CA"), ["Interne", "Externe"])

    def test_prereglages_nommes_dans_la_langue_de_la_societe_anglaise(self):
        self.assertEqual(self._prereglages("en_CA", "fr_CA"), ["Internal", "External"])

    def test_prereglages_societe_en_langue_inactive_repli_installe(self):
        """nextcloud_document_config.py ~428 : la société garde le code d'une langue désactivée
        depuis (de_DE, posé en SQL : l'ORM le refuse). Plus d'« Invalid language code » : la
        langue de l'appel, installée, nomme les préréglages."""
        if "de_DE" in [code for code, _nom in self.env["res.lang"].get_installed()]:
            self.skipTest("de_DE est active sur cette base")
        self.env.cr.execute("UPDATE res_partner SET lang = 'de_DE' WHERE id = %s",
                            (self.config.company_id.partner_id.id,))
        self.env.invalidate_all()
        self.config.share_preset_ids.unlink()
        self.config.with_context(lang="fr_CA")._ensure_share_presets()
        self.assertEqual(self.config.share_preset_ids.sorted("sequence").mapped("name"), ["Interne", "Externe"])

    # ------------------------------------------------------------------
    # controllers/main.py:59 : nom de fichier par défaut
    # ------------------------------------------------------------------
    def test_nom_de_fichier_par_defaut_en_anglais(self):
        """Chemin sans nom (« /Equipe/ ») : le fichier téléchargé s'appelle « file », plus « fichier »."""
        from odoo.addons.bf_nextcloud_browser.controllers import main as controleur
        with patch.object(type(self.config), "_webdav_get", return_value=b"x"), \
                patch.object(controleur, "request", new=MagicMock()) as requete:
            # `new=` : le mandataire `request` n'est pas lié hors d'une requête HTTP.
            requete.make_response.side_effect = lambda content, headers: headers
            entetes = dict(controleur.BfNcBrowserController()._stream(self.config, PREFIX, True))
        self.assertEqual(entetes["Content-Disposition"], "attachment; filename*=UTF-8''file")

    # ------------------------------------------------------------------
    # bf.nc.user.credential._compute_display_name (bf_nc_user_credential.py:123-127)
    # ------------------------------------------------------------------
    def test_connexion_en_attente_nommee_dans_la_langue_du_lecteur(self):
        """« en attente » / « pending », et le nom n'est pas gardé d'une langue à l'autre
        (`depends_context('lang')`)."""
        bob = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Bob Essai", "login": "bob@langue.invalid", "lang": "fr_CA"})
        cred = self.env["bf.nc.user.credential"].sudo().create({"user_id": bob.id, "config_id": self.config.id})
        self.assertEqual(cred.with_context(lang="en_CA").display_name, "Bob Essai -> pending")
        self.assertEqual(cred.with_context(lang="fr_CA").display_name, "Bob Essai -> en attente")
        self.assertEqual(cred.with_context(lang="en_US").display_name, "Bob Essai -> pending")

    # ------------------------------------------------------------------
    # Migration 18.0.4.4.0 : nom et commentaire du groupe (noupdate)
    # ------------------------------------------------------------------
    def _migration(self, etape):
        chemin = get_module_path("bf_nextcloud_browser") + f"/migrations/18.0.4.4.0/{etape}-migrate.py"
        spec = importlib.util.spec_from_file_location(f"bf_nc_browser_mig_{etape}", chemin)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _groupe(self, champ, lang):
        groupe = self.env.ref("bf_nextcloud_browser.group_nc_browser_user")
        self.env.cr.execute(f"SELECT {champ}->>%s FROM res_groups WHERE id = %s", (lang, groupe.id))
        return self.env.cr.fetchone()[0]

    def _poser_groupe(self, champ, valeur):
        groupe = self.env.ref("bf_nextcloud_browser.group_nc_browser_user")
        self.env.cr.execute(f"UPDATE res_groups SET {champ} = %s::jsonb WHERE id = %s",
                            (__import__("json").dumps(valeur), groupe.id))
        self.env.invalidate_all()

    def _monter(self):
        for etape in ("post", "end"):
            self._migration(etape).migrate(self.env.cr, "18.0.4.3.0")
        self.env.invalidate_all()

    def test_migration_18_0_4_4_0_groupe_nom_et_commentaire_livres(self):
        """Le français livré en en_US passe à l'anglais; le français va en fr_CA."""
        self._poser_groupe("name", {"en_US": "Navigateur Nextcloud"})
        self._poser_groupe("comment", {"en_US": "Donne accès au navigateur de fichiers Nextcloud "
                                                "embarqué dans les projets et tâches."})
        self._monter()
        self.assertEqual(self._groupe("name", "en_US"), "Nextcloud browser")
        self.assertEqual(self._groupe("name", "fr_CA"), "Navigateur Nextcloud")
        self.assertEqual(self._groupe("comment", "en_US"),
                         "Gives access to the Nextcloud file browser embedded in projects and tasks.")
        self.assertTrue(self._groupe("comment", "fr_CA").startswith("Donne accès au navigateur"))

    def test_migration_18_0_4_4_0_groupe_retouche_gardee(self):
        """Un nom retouché à la main reste, et le catalogue ne le masque pas en fr_CA."""
        self._poser_groupe("name", {"en_US": "Fichiers de l'équipe"})
        self._monter()
        self.assertEqual(self._groupe("name", "en_US"), "Fichiers de l'équipe")
        self.assertEqual(self._groupe("name", "fr_CA"), "Fichiers de l'équipe")

