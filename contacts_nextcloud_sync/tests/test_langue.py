"""La langue de ce que le module écrit à qui le lit.

La source est anglaise et le français vient de `i18n/fr_CA.po`. Jusqu'à la 18.0.1.3.0, les
messages de l'essai de connexion, les résumés d'envoi et de lecture et le titre des
notifications étaient des f-strings : ils se lisaient en anglais pour tout le monde, quelle
que soit la langue de l'usager. Chaque essai lit le MÊME geste dans les deux langues et
exige qu'elles diffèrent — un essai qui ne compare qu'une langue passerait sur une source
restée anglaise.

🔴 Aucun essai ne touche le réseau : chaque chemin éprouvé sort avant l'appel CardDAV.
"""
import ast
from pathlib import Path
from unittest.mock import patch

import odoo
from odoo import SUPERUSER_ID
from odoo.tests import TransactionCase, tagged


@tagged("contacts_nextcloud_sync", "post_install", "-at_install")
class TestLangue(TransactionCase):

    MODULE = "contacts_nextcloud_sync"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("en_US", "fr_CA"):
            cls.env["res.lang"]._activate_lang(code)
        # Sur une base neuve, fr_CA n'est pas chargé : sans effet là où il l'est.
        cls.env["ir.module.module"]._load_module_terms(
            ["contacts_nextcloud_sync"], ["fr_CA"], overwrite=True)
        cls.etiquette = cls.env["res.partner.category"].create({"name": "Hors synchro"})
        cls.config = cls.env["nextcloud.contacts.sync.config"].create({
            "name": "Carnet d'essai",
            "nextcloud_base_url": "https://nc.invalid",
            "carddav_path": "/remote.php/dav/addressbooks/users/essai/contacts/",
            "nextcloud_user": "essai",
            "exclude_tag_id": cls.etiquette.id,
        })

    def _notification(self, lang, methode, **valeurs):
        config = self.config.with_context(lang=lang)
        if valeurs:
            config.write(valeurs)
        action = getattr(config, methode)()
        return action["params"]["title"], action["params"]["message"]

    def test_essai_de_connexion_sans_mot_de_passe(self):
        """Le message ET le titre suivent la langue de qui a cliqué."""
        titre_fr, message_fr = self._notification("fr_CA", "action_test_connection")
        titre_en, message_en = self._notification("en_US", "action_test_connection")
        self.assertEqual(titre_fr, "Synchronisation des contacts")
        self.assertEqual(titre_en, "Contacts Sync")
        self.assertIn("mot de passe d'application", message_fr)
        self.assertIn("app password", message_en)
        self.assertNotEqual(message_fr, message_en)

    def test_statut_ecrit_dans_la_langue_de_qui_agit(self):
        """`last_sync_message` est écrit par le geste : il suit sa langue."""
        self.config.with_context(lang="fr_CA").action_test_connection()
        self.assertEqual(self.config.last_sync_status, "error")
        self.assertIn("Aucun mot de passe", self.config.last_sync_message)
        self.config.with_context(lang="en_US").action_test_connection()
        self.assertIn("No app password", self.config.last_sync_message)

    def test_garde_du_sens_de_synchronisation(self):
        """Les deux refus de sens sont traduits, et ne se confondent pas."""
        _t, envoi_fr = self._notification(
            "fr_CA", "action_push_to_nextcloud", sync_direction="nc_to_odoo")
        _t, envoi_en = self._notification(
            "en_US", "action_push_to_nextcloud", sync_direction="nc_to_odoo")
        _t, lecture_fr = self._notification(
            "fr_CA", "action_pull_from_nextcloud", sync_direction="odoo_to_nc")
        self.assertIn("rien n'est envoyé", envoi_fr)
        self.assertIn("Cannot push", envoi_en)
        self.assertIn("rien n'est lu", lecture_fr)
        self.assertNotEqual(envoi_fr, lecture_fr)

    def test_libelle_de_champ_et_travail_planifie(self):
        """Le catalogue atteint aussi les libellés de champ et les données livrées."""
        champ = self.env["ir.model.fields"].search([
            ("model", "=", "nextcloud.contacts.sync.config"), ("name", "=", "name"),
        ], limit=1)
        self.assertEqual(self._mesurer_le_catalogue(champ, "field_description"),
                         "Nom du carnet d'adresses")
        self.assertEqual(
            champ.with_context(lang="en_US").field_description, "Address Book Name")
        for xmlid, attendu in (
            ("contacts_nextcloud_sync.cron_nextcloud_contacts_sync",
             "Synchronisation des contacts Nextcloud"),
            ("contacts_nextcloud_sync.cron_nextcloud_contacts_full_resync",
             "Resynchronisation complète des contacts Nextcloud"),
        ):
            cron = self.env.ref(xmlid)
            self.assertEqual(self._mesurer_le_catalogue(cron, "name"), attendu)
    # ── 🔴 Le piège du travail planifié (relu une seconde fois) ──────

    def test_aucune_fonction_qui_traduit_ne_lie_un_nom_piege(self):
        """`_()` sans langue au contexte lit `frame.f_locals['uid']` VERBATIM.

        `odoo/tools/translate.py` (`_get_uid`) rend ce local tel quel, sans `int()` ni
        contrôle, et `_get_lang` n'y arrive que si `self.env.lang` est faux — ce qui est
        le cas d'un travail planifié, dont l'environnement est bâti sans langue. Un local
        nommé `uid` qui porte l'UUID d'une vCard fait donc construire
        `res.users('f','d','9',…)` et lever. Même piège pour un local nommé `user`, que
        `_get_uid` passe à `int()`.

        Cette garde est statique parce que le défaut est une COÏNCIDENCE DE NOM : elle
        tient quel que soit le chemin d'exécution, et elle protège le prochain `_()`
        ajouté dans ce fichier.
        """
        # Les SIX noms que lit le dernier recours d'Odoo, dans l'ordre
        # (`tools/translate.py` : `_get_lang` puis `_get_cr` et `_get_uid`) : un local
        # `context` non-dict, un `cr`/`cursor` qui n'est pas un curseur, un `uid` rendu
        # VERBATIM, un `user` passé à `int()`.
        pieges = {"uid", "user", "context", "cr", "cursor", "kwargs"}
        enveloppes = {"_", "_lt"}
        racine = Path(__file__).resolve().parent.parent
        coupables = []
        for fichier in sorted(racine.rglob("*.py")):
            if "tests" in fichier.parts or "__pycache__" in fichier.parts:
                continue
            arbre = ast.parse(fichier.read_text(encoding="utf-8"))
            for fn in [n for n in ast.walk(arbre)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                traduit = any(isinstance(c, ast.Call)
                              and (getattr(c.func, "id", None) in enveloppes
                                   or getattr(c.func, "attr", None) in enveloppes)
                              for c in ast.walk(fn))
                if not traduit:
                    continue
                # ⛔ Les LOCAUX seulement, pas les paramètres : une signature est
                # un contrat. `bf_preferred_name_nextcloud` surcharge
                # `_partner_to_vcard(self, partner, uid=None)` et appelle
                # `super()(…, uid=uid)` — renommer le paramètre lèverait TypeError à
                # chaque poussée d'une fiche à pronoms.
                lies = {n.id for n in ast.walk(fn)
                        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
                for piege in sorted(pieges & lies):
                    coupables.append(f"{fichier.name}:{fn.lineno} {fn.name}() lie « {piege} »")
        self.assertEqual(
            coupables, [],
            "une fonction qui appelle _() lie un nom que _get_uid d'Odoo lit verbatim ; "
            "renommer le local (vcard_uid, nc_user…)")

    def test_resume_de_poussee_sous_un_environnement_de_travail_planifie(self):
        """Le chemin réel : pousser au moins un contact, sans langue au contexte.

        C'est la frame qui plantait : `action_push_to_nextcloud` lie `vcard_uid` puis
        appelle `_()` pour son résumé. L'environnement est bâti comme `ir_cron` le bâtit
        (`api.Environment(cr, user_id, {})`, donc `lang` nul), et la couche CardDAV est
        bouchonnée : aucun octet ne sort.
        """
        cron_env = odoo.api.Environment(self.env.cr, SUPERUSER_ID, {})
        self.assertFalse(cron_env.lang, "l'environnement d'un cron n'a pas de langue")
        contact = self.env["res.partner"].create({
            "name": "Contact du travail planifié", "type": "contact",
            "email": "contact.cron@test.invalid",
        })
        config = self.config.with_env(cron_env)
        config.write({"sync_direction": "odoo_to_nc",
                      "nextcloud_app_password": "mot-de-passe-bouchon"})
        Config = type(config)
        # ⚠️ `action_push_to_nextcloud` commite deux fois (l. 512 et 522) pour ne pas
        # perdre les etags d'une passe longue. Le curseur d'essai d'Odoo remplace
        # `commit` par un `forbidden` posé sur l'INSTANCE (pas sur la classe) : c'est
        # donc l'instance qu'on neutralise, le temps de l'appel. Le `with` referme
        # tout, y compris en cas d'échec.
        with patch.object(Config, "_carddav_put_vcard", return_value='"etag-bouchon"'), \
                patch.object(Config, "_carddav_propfind_vcards", return_value=[]), \
                patch.object(Config, "_carddav_delete_vcard", return_value=None), \
                patch.object(self.env.cr, "commit", lambda: None):
            action = config.action_push_to_nextcloud()
        # 🔴 Ces trois affirmations-ci ne mesuraient RIEN : « params » existe aussi
        # sur « 644 inchangé(s), 2 erreur(s) », et `contact.exists()` est trivial. Le
        # module a traversé 141 essais, 24 mutations et deux parcours au navigateur avec
        # `pull` mort et `push` stérile, sans qu'un seul ne tombe. On affirme
        # maintenant que la poussée a POUSSÉ.
        self.assertIn("params", action)
        self.assertEqual(config.last_sync_status, "success",
                         f"la poussée a rapporté : {config.last_sync_message!r}")
        self.assertNotIn("erreur", config.last_sync_message.lower())
        self.assertNotIn("error", config.last_sync_message.lower())
        self.assertEqual(contact.x_nc_contact_uid and True, True,
                         "le contact doit avoir reçu son UID de vCard")
        self.assertEqual(contact.x_nc_contacts_config_id, config,
                         "et être rattaché au carnet")
        self.assertEqual(contact.x_contact_sync_source, "odoo")

    def test_lecture_depuis_nextcloud_sous_un_environnement_de_travail_planifie(self):
        """La LECTURE, que rien n'éprouvait — c'est elle qui mourait.

        `action_pull_from_nextcloud` lève HORS de tout `try` si un nom de variable ne
        correspond pas : la lecture entière meurt, sans qu'aucun essai ne tombe. Ici une
        vCard bouchonnée suffit à parcourir tout le chemin.
        """
        cron_env = odoo.api.Environment(self.env.cr, SUPERUSER_ID, {})
        config = self.config.with_env(cron_env)
        config.write({"sync_direction": "nc_to_odoo",
                      "nextcloud_app_password": "mot-de-passe-bouchon"})
        vcard = (
            "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:essai-uid\r\n"
            "FN:Lecture Du Cron\r\nEMAIL;TYPE=INTERNET:lecture@test.invalid\r\n"
            "END:VCARD\r\n"
        )
        Config = type(config)
        with patch.object(Config, "_carddav_propfind_vcards", return_value=[
                    {"uid": "essai-uid", "etag": '"e1"',
                     "href": "/dav/essai-uid.vcf"}]), \
                patch.object(Config, "_carddav_get_vcard",
                             return_value=(vcard, '"e1"')), \
                patch.object(self.env.cr, "commit", lambda: None):
            action = config.action_pull_from_nextcloud()
        self.assertIn("params", action)
        self.assertEqual(config.last_sync_status, "success",
                         f"la lecture a rapporté : {config.last_sync_message!r}")
        pose = self.env["res.partner"].search(
            [("x_nc_contact_uid", "=", "essai-uid")], limit=1)
        self.assertTrue(pose, "la vCard lue doit avoir créé son contact")
        # ⚠️ Ici l'etag est stocké TEL QUEL : il vient du bouchon de
        # `_carddav_propfind_vcards`, pas de `_parse_propfind_entries`, qui est le seul à
        # dépouiller les guillemets. Les deux chemins diffèrent, et c'est mesuré.
        self.assertEqual(pose.x_carddav_etag, '"e1"')
        self.assertEqual(pose.x_contact_sync_source, "nextcloud")

        # 🔴 Deuxième passe, etag changé : c'est la branche « contact DÉJÀ connu », qui
        # emprunte un autre chemin de code que la création. Sans elle, la mutation de ce
        # chemin survivait : l'essai ne créait que du neuf.
        vcard2 = vcard.replace("FN:Lecture Du Cron", "FN:Lecture Du Cron, corrigée")
        with patch.object(Config, "_carddav_propfind_vcards", return_value=[
                    {"uid": "essai-uid", "etag": '"e2"',
                     "href": "/dav/essai-uid.vcf"}]), \
                patch.object(Config, "_carddav_get_vcard",
                             return_value=(vcard2, '"e2"')), \
                patch.object(self.env.cr, "commit", lambda: None):
            action2 = config.action_pull_from_nextcloud()
        self.assertIn("mis à jour", action2["params"]["message"],
                      f"la 2e passe a rapporté : {action2['params']['message']!r}")
        encore = self.env["res.partner"].search(
            [("x_nc_contact_uid", "=", "essai-uid")])
        self.assertEqual(len(encore), 1, "la 2e passe ne doit pas dupliquer le contact")
        self.assertEqual(encore.x_carddav_etag, '"e2"')

        # 🔴 Troisième branche, la dernière du chemin de lecture : une vCard dont le
        # courriel apparie un contact Odoo PAS ENCORE synchronisé. Elle adopte la fiche
        # au lieu d'en créer une seconde. C'est elle que la mutation de ce chemin
        # vise, et sans ce cas-ci elle survivait.
        a_adopter = self.env["res.partner"].create({
            "name": "Fiche à adopter", "type": "contact",
            "email": "adopter@test.invalid",
        })
        self.assertFalse(a_adopter.x_nc_contacts_config_id)
        vcard3 = (
            "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:adopter-uid\r\n"
            "FN:Fiche A Adopter\r\n"
            "EMAIL;TYPE=INTERNET:adopter@test.invalid\r\nEND:VCARD\r\n"
        )
        with patch.object(Config, "_carddav_propfind_vcards", return_value=[
                    {"uid": "adopter-uid", "etag": '"e3"',
                     "href": "/dav/adopter-uid.vcf"}]), \
                patch.object(Config, "_carddav_get_vcard",
                             return_value=(vcard3, '"e3"')), \
                patch.object(self.env.cr, "commit", lambda: None):
            action3 = config.action_pull_from_nextcloud()
        self.assertEqual(config.last_sync_status, "success",
                         f"la 3e passe a rapporté : {action3['params']['message']!r}")
        a_adopter.invalidate_recordset()
        self.assertEqual(a_adopter.x_nc_contact_uid, "adopter-uid",
                         "la fiche existante doit avoir été ADOPTÉE, pas dupliquée")
        self.assertEqual(
            self.env["res.partner"].search_count(
                [("email", "=ilike", "adopter@test.invalid")]), 1,
            "aucun doublon ne doit avoir été créé")

    def test_le_listage_carddav_rend_ses_uid(self):
        """`_parse_propfind_entries` est la porte d'entrée de TOUT listage CardDAV : la
        poussée, la lecture et la resynchronisation complète en dépendent. Elle mourait
        sans qu'aucun essai ne la touche."""
        xml = (
            '<?xml version="1.0"?>'
            '<d:multistatus xmlns:d="DAV:" xmlns:cs="http://calendarserver.org/ns/">'
            '  <d:response><d:href>/dav/alpha.vcf</d:href><d:propstat><d:prop>'
            '    <d:getetag>"etag-alpha"</d:getetag>'
            '  </d:prop></d:propstat></d:response>'
            '  <d:response><d:href>/dav/beta.vcf</d:href><d:propstat><d:prop>'
            '    <d:getetag>"etag-beta"</d:getetag>'
            '  </d:prop></d:propstat></d:response>'
            '</d:multistatus>'
        )
        entrees = self.config._parse_propfind_entries(xml)
        self.assertEqual([e["uid"] for e in entrees], ["alpha", "beta"])
        # ⚠️ Le module DÉPOUILLE les guillemets de l'etag HTTP : mesuré ici, pas supposé.
        self.assertEqual([e["etag"] for e in entrees], ["etag-alpha", "etag-beta"])

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
        """Le défaut visé : la fiche du module dans Apps, lue en anglais au Québec."""
        module = self.env["ir.module.module"].search(
            [("name", "=", "contacts_nextcloud_sync")], limit=1)
        self.assertEqual(
            self._mesurer_le_catalogue(module, "shortdesc"),
            "Synchronisation des contacts Nextcloud")
        self.assertEqual(
            module.with_context(lang="en_US").shortdesc, "Contacts Nextcloud Sync")
        self.assertIn("carnet d'adresses", self._mesurer_le_catalogue(module, "summary"))
