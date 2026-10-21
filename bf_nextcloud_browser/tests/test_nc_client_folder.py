"""Client folders (18.0.4.2.0).

The folder belongs to the company; a project takes a subfolder of it, a task
follows its project, a contact shows the folder of its company, and the
systray panel opens on the folder of the record on screen.

No network: `requests` is patched and the Fernet key too, as in test_nc_links.
A PROPFIND 404 is raised as NcPathNotFound by a response hook in real life; a
patched `requests.request` runs no hook, so the fake raises it itself.
"""

import unicodedata
from unittest.mock import MagicMock, patch
from urllib.parse import quote

from psycopg2 import IntegrityError

from cryptography.fernet import Fernet

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.bf_nextcloud_browser.models import nc_match
from odoo.addons.bf_nextcloud_browser.models.nc_identity import NcPathNotFound

KEY = Fernet.generate_key()
BASE = "https://nc.example.test"
PREFIX = "/Équipe/"
CLIENTS = "/Équipe/(Clients)"
DAV = "/remote.php/dav/files/alice@corp"


def _response(status, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.json.return_value = {}
    return resp


def _multistatus(folder, *children, dav=DAV):
    """PROPFIND depth 1 of `folder`, as seen by the account of `dav`: the folder
    itself, then its child folders."""
    parts = []
    for i, path in enumerate((folder,) + children):
        href = quote(dav + path + "/")
        parts.append(
            "<d:response><d:href>%s</d:href><d:propstat><d:prop>"
            "<d:resourcetype><d:collection/></d:resourcetype><oc:fileid>%d</oc:fileid>"
            "<oc:size>0</oc:size>"
            "<d:getlastmodified>Mon, 14 Sep 2026 12:00:00 GMT</d:getlastmodified>"
            "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
            % (href, 100 + i)
        )
    return (
        '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
        + "".join(parts) + "</d:multistatus>"
    )


@tagged("post_install", "-at_install")
class TestNcClientFolder(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # French expectations: a fresh base (the public CI) has en_US only, so fr_CA
        # and its terms are loaded here.
        cls.env["res.lang"]._activate_lang("fr_CA")
        cls.env["ir.module.module"]._load_module_terms(['bf_nextcloud_browser'], ["fr_CA"], overwrite=True)
        Config = cls.env["nextcloud.document.config"]
        cls._key_patch = patch.object(type(Config), "_get_encryption_key", return_value=KEY)
        cls._key_patch.start()
        cls.addClassCleanup(cls._key_patch.stop)
        cls.config = Config.create({
            "name": "Essai dossiers",
            "nextcloud_base_url": BASE,
            "webdav_path": "/remote.php/dav/files/",
            "nextcloud_user": "service",
            "nextcloud_app_password": "service-secret",
            "browser_root_prefix": PREFIX,
            "nc_clients_root": CLIENTS + "/",
            "nc_projects_subfolder": "(PROJETS)",
        })
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_document_nextcloud_sync.default_config_id", cls.config.id
        )
        group = cls.env.ref("bf_nextcloud_browser.group_nc_browser_user")
        users = cls.env["res.users"].with_context(no_reset_password=True)
        internal = cls.env.ref("base.group_user")
        project_user = cls.env.ref("project.group_project_manager")
        partner_manager = cls.env.ref("base.group_partner_manager")
        cls.alice = users.create({
            "name": "Alice", "login": "alice@dossiers.invalid",
            "groups_id": [(6, 0, [internal.id, group.id, project_user.id, partner_manager.id])],
        })
        cls.carol = users.create({  # internal, not in the browser group
            "name": "Carol", "login": "carol@dossiers.invalid",
            "groups_id": [(6, 0, [internal.id, project_user.id])],
        })
        cls.env["bf.nc.user.credential"].sudo().create({
            "user_id": cls.alice.id,
            "config_id": cls.config.id,
            "state": "connected",
            "nc_login": "alice@corp",
            "nc_user_id": "alice@corp",
            "nc_display_name": "Alice",
            "nc_server": BASE,
            "app_password_encrypted": cls.config._encrypt_value("pw-a"),
        })
        Partner = cls.env["res.partner"]
        cls.valberge = Partner.create({"name": "Toitures Valberge inc.", "is_company": True})
        cls.gaston = Partner.create({"name": "Gaston Vérillac", "parent_id": cls.valberge.id})
        cls.asvb = Partner.create({"name": "1234-5678 Québec Inc.", "is_company": True})
        cls.anne_sophie = Partner.create({"name": "Anne-Sophie Valmorel Brideaux", "parent_id": cls.asvb.id})
        cls.other = Partner.create({"name": "Autre client", "is_company": True})
        cls.Folder = cls.env["bf.nc.client.folder"]
        cls.browser = cls.env["bf.nc.browser"].with_user(cls.alice)

    def _folder(self, partner, name, **vals):
        return self.Folder.create({"partner_id": partner.id, "path": CLIENTS + "/" + name, **vals})

    # ------------------------------------------------------------------
    # The folder record
    # ------------------------------------------------------------------
    def test_a_folder_is_stored_clean_and_named_after_its_last_segment(self):
        f = self.Folder.create({"partner_id": self.valberge.id, "path": " " + CLIENTS + "/041 - Toitures Valberge/ "})
        self.assertEqual(f.path, CLIENTS + "/041 - Toitures Valberge")
        self.assertEqual(f.name, "041 - Toitures Valberge")

    def test_a_folder_given_to_a_contact_goes_to_its_company(self):
        f = self._folder(self.gaston, "041 - Toitures Valberge")
        self.assertEqual(f.partner_id, self.valberge)

    def test_the_root_a_path_outside_the_prefix_and_a_decoding_name_are_refused(self):
        for bad in ("/", "/Ailleurs/Client", CLIENTS + "/%2e%2e"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.Folder.create({"partner_id": self.valberge.id, "path": bad})

    def test_one_folder_belongs_to_one_client(self):
        self._folder(self.valberge, "041 - Toitures Valberge")
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError), self.cr.savepoint():
            self._folder(self.other, "041 - Toitures Valberge")

    def test_the_same_name_in_another_unicode_form_is_the_same_folder(self):
        nfd = unicodedata.normalize("NFD", "009 - Chantier Sérénité")
        f = self._folder(self.asvb, nfd)
        self.assertEqual(f.path, CLIENTS + "/" + unicodedata.normalize("NFC", nfd))
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError), self.cr.savepoint():
            self._folder(self.other, "009 - Chantier Sérénité")

    def test_folders_cannot_nest_nor_be_the_browser_root(self):
        # Alone, the clients root itself: it would hold every client to come.
        with self.assertRaises(ValidationError):
            self.Folder.create({"partner_id": self.other.id, "path": CLIENTS + "/"})
        with self.assertRaises(ValidationError):
            self.Folder.create({"partner_id": self.other.id, "path": PREFIX})
        self._folder(self.valberge, "041 - Toitures Valberge")
        with self.assertRaises(ValidationError):
            self._folder(self.other, "041 - Toitures Valberge/Sous-traitant")

    # ------------------------------------------------------------------
    # Partner, project, task
    # ------------------------------------------------------------------
    def test_a_contact_shows_the_first_folder_of_its_company(self):
        self.assertFalse(self.valberge.nc_browser_folder)
        self._folder(self.asvb, "009 - Chantier Sérénité", sequence=20)
        self._folder(self.asvb, "043 - Anne-Sophie Valmorel Brideaux (perso)", sequence=10)
        self.assertEqual(self.asvb.nc_browser_folder, CLIENTS + "/043 - Anne-Sophie Valmorel Brideaux (perso)")
        self.assertEqual(self.anne_sophie.nc_browser_folder, self.asvb.nc_browser_folder)

    def test_the_company_tab_waits_for_a_usable_configuration_and_a_clients_root(self):
        """18.0.4.3.2: without them, the tab of every company only offered two
        buttons leading to an error (a tenant without client folders)."""
        valberge = self.valberge.with_user(self.alice)

        def enabled():
            self.env.invalidate_all()
            return valberge.nc_client_folders_enabled

        self.assertTrue(enabled())
        # The creation button also needs a template.
        self.assertFalse(self.config.nc_client_template)
        self.assertFalse(valberge.nc_client_folder_creation_enabled)
        self.config.nc_client_template = CLIENTS + "/(DOSSIER TYPE)"
        self.env.invalidate_all()
        self.assertTrue(valberge.nc_client_folder_creation_enabled)
        self.config.nc_client_template = False
        self.config.nc_clients_root = False
        self.assertFalse(enabled())
        self.config.nc_clients_root = CLIENTS + "/"
        self.assertTrue(enabled())
        self.config.browser_root_prefix = "/"  # the browser does not run on "/"
        self.assertFalse(enabled())
        self.config.browser_root_prefix = PREFIX
        self.assertTrue(enabled())
        # Archived but still the default (the settings keep its id): the browser no
        # longer runs on it, nor do client folders.
        self.config.active = False
        self.assertFalse(enabled())
        self.config.active = True
        self.assertTrue(enabled())
        self.env["ir.config_parameter"].sudo().set_param("bf_document_nextcloud_sync.default_config_id", False)
        # Every configuration, not only ours: a database may hold its own.
        self.env["nextcloud.document.config"].sudo().search([]).active = False
        self.assertFalse(enabled())

        arch = valberge.get_views([(False, "form")])["views"]["form"]["arch"]
        self.assertIn('invisible="nc_client_folder_ids or not nc_client_folder_creation_enabled"', arch)
        self.assertIn("not nc_client_folders_enabled and not nc_client_folder_ids", arch)
        self.assertIn('invisible="parent_id or not nc_client_folders_enabled"', arch)

    def test_a_project_takes_its_clients_only_folder_then_its_subfolder(self):
        project = self.env["project.project"].create({"name": "Valberge - Agrandissement hangar", "partner_id": self.gaston.id})
        self.assertFalse(project.nc_browser_folder)
        self.assertFalse(project.nc_client_folder_available)
        folder = self._folder(self.valberge, "041 - Toitures Valberge")
        self.assertEqual(project.nc_client_folder_id, folder)
        self.assertTrue(project.nc_client_folder_available)
        self.assertEqual(project.nc_browser_folder, folder.path)
        project.nc_project_subfolder = "/(PROJETS)/001 - Agrandissement hangar/"
        self.assertEqual(project.nc_browser_folder, folder.path + "/(PROJETS)/001 - Agrandissement hangar")

    def test_a_subfolder_cannot_climb_out_of_the_client_folder(self):
        folder = self._folder(self.valberge, "041 - Toitures Valberge")
        project = self.env["project.project"].create({"name": "P", "partner_id": self.valberge.id})
        project.nc_project_subfolder = "../../Autre client"
        self.assertEqual(project.nc_browser_folder, folder.path + "/Autre client")
        self.assertEqual(project.nc_project_subfolder, "Autre client")  # stored clean

    def test_a_decoding_subfolder_is_refused_when_typed_on_a_project_or_a_task(self):
        self._folder(self.valberge, "041 - Toitures Valberge")
        project = self.env["project.project"].create({"name": "P", "partner_id": self.valberge.id})
        task = self.env["project.task"].create({"name": "T", "project_id": project.id})
        for bad in ("%2e%2e", "a%252e", "x\x01y"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    project.nc_project_subfolder = bad
                with self.assertRaises(ValidationError):
                    task.nc_task_subfolder = bad

    def test_the_subfolder_does_not_follow_a_change_of_client_made_outside_the_form(self):
        self._folder(self.valberge, "041 - Toitures Valberge")
        other = self._folder(self.other, "040 - Autre client")
        project = self.env["project.project"].create({
            "name": "P", "partner_id": self.valberge.id, "nc_project_subfolder": "(PROJETS)/001 - P",
        })
        project.write({"partner_id": self.other.id})  # RPC, import, merge
        self.assertEqual(project.nc_client_folder_id, other)
        self.assertFalse(project.nc_project_subfolder)
        self.assertEqual(project.nc_browser_folder, other.path)

    def test_deleting_the_chosen_folder_drops_its_subfolder(self):
        perso = self._folder(self.asvb, "043 - Anne-Sophie Valmorel Brideaux (perso)")
        chantier = self._folder(self.asvb, "009 - Chantier Sérénité")
        project = self.env["project.project"].create({"name": "P", "partner_id": self.asvb.id})
        project.write({"nc_client_folder_id": chantier.id, "nc_project_subfolder": "(PROJETS)/001 - M"})
        chantier.unlink()
        self.assertEqual(project.nc_client_folder_id, perso)
        self.assertFalse(project.nc_project_subfolder)

    def test_deleting_a_folder_drops_the_subfolder_of_archived_projects_too(self):
        perso = self._folder(self.asvb, "043 - Anne-Sophie Valmorel Brideaux (perso)")
        chantier = self._folder(self.asvb, "009 - Chantier Sérénité")
        project = self.env["project.project"].create({"name": "P", "partner_id": self.asvb.id})
        project.write({"nc_client_folder_id": chantier.id, "nc_project_subfolder": "(PROJETS)/001 - M"})
        project.active = False
        chantier.unlink()
        self.assertEqual(project.nc_client_folder_id, perso)
        self.assertFalse(project.nc_project_subfolder)

    def test_a_duplicated_project_does_not_open_the_source_projects_subfolder(self):
        self._folder(self.valberge, "041 - Toitures Valberge")
        project = self.env["project.project"].create({
            "name": "P", "partner_id": self.valberge.id, "nc_project_subfolder": "(PROJETS)/001 - P",
        })
        copy = project.copy()
        self.assertFalse(copy.nc_project_subfolder)

    def test_the_folder_typed_on_the_project_still_wins(self):
        self._folder(self.valberge, "041 - Toitures Valberge")
        project = self.env["project.project"].create({"name": "P", "partner_id": self.valberge.id})
        project.nc_documents_folder = "/Équipe/Interne/P"
        self.assertEqual(project.nc_browser_folder, "/Équipe/Interne/P")

    def test_a_choice_among_several_folders_is_kept_when_another_is_added(self):
        perso = self._folder(self.asvb, "043 - Anne-Sophie Valmorel Brideaux (perso)")
        project = self.env["project.project"].create({"name": "Entretien", "partner_id": self.asvb.id})
        self.assertEqual(project.nc_client_folder_id, perso)
        chantier = self._folder(self.asvb, "009 - Chantier Sérénité")
        project.nc_client_folder_id = chantier
        self._folder(self.asvb, "030 - Encore")
        self.assertEqual(project.nc_client_folder_id, chantier)

    def test_a_project_moved_to_another_client_takes_that_clients_folder(self):
        self._folder(self.valberge, "041 - Toitures Valberge")
        other = self._folder(self.other, "040 - Autre client")
        project = self.env["project.project"].create({"name": "P", "partner_id": self.valberge.id})
        project.partner_id = self.other
        self.assertEqual(project.nc_client_folder_id, other)

    def test_a_task_follows_its_project_and_its_own_subfolder(self):
        folder = self._folder(self.valberge, "041 - Toitures Valberge")
        project = self.env["project.project"].create({
            "name": "P", "partner_id": self.valberge.id, "nc_project_subfolder": "(PROJETS)/001 - P",
        })
        task = self.env["project.task"].create({"name": "T", "project_id": project.id})
        self.assertEqual(task.nc_browser_folder, folder.path + "/(PROJETS)/001 - P")
        task.nc_task_subfolder = "Livrables"
        self.assertEqual(task.nc_browser_folder, folder.path + "/(PROJETS)/001 - P/Livrables")
        self.assertEqual(task.nc_documents_folder, task.nc_browser_folder)

    # ------------------------------------------------------------------
    # The browser on a partner
    # ------------------------------------------------------------------
    def test_the_browser_lists_the_client_folder_from_the_contact_as_the_person(self):
        folder = self._folder(self.valberge, "041 - Toitures Valberge")
        body = _multistatus(folder.path, folder.path + "/(PROJETS)")
        with patch("requests.request", return_value=_response(207, body)) as req:
            res = self.browser.browse_dir("res.partner", self.gaston.id, "")
        method, url = req.call_args.args[:2]
        self.assertEqual(method, "PROPFIND")
        self.assertTrue(url.startswith(BASE + "/remote.php/dav/files/alice"), url)
        self.assertTrue(url.endswith(quote(folder.path)), url)
        self.assertEqual(req.call_args.kwargs["auth"].username, "alice@corp")
        self.assertEqual([e["name"] for e in res["entries"]], ["(PROJETS)"])

    def test_a_partner_without_folder_and_a_person_outside_the_group_are_refused(self):
        with patch("requests.request") as req:
            with self.assertRaises(UserError):
                self.browser.browse_dir("res.partner", self.other.id, "")
            self._folder(self.valberge, "041 - Toitures Valberge")
            with self.assertRaises(AccessError):
                self.env["bf.nc.browser"].with_user(self.carol).browse_dir("res.partner", self.valberge.id, "")
        req.assert_not_called()

    def test_the_matrix_targets_of_a_partner_do_not_break(self):
        self._folder(self.valberge, "041 - Toitures Valberge")
        res = self.browser.knowledge_targets("res.partner", self.valberge.id)
        self.assertEqual(res["items"], [])

    # ------------------------------------------------------------------
    # The systray panel follows the record on screen
    # ------------------------------------------------------------------
    def test_the_panel_opens_on_the_folder_of_a_partner_project_task_or_any_linked_record(self):
        folder = self._folder(self.valberge, "041 - Toitures Valberge")
        project = self.env["project.project"].create({
            "name": "P", "partner_id": self.valberge.id, "nc_project_subfolder": "(PROJETS)/001 - P",
        })
        task = self.env["project.task"].create({"name": "T", "project_id": project.id})
        rel_client = "(Clients)/041 - Toitures Valberge"
        with patch("requests.request") as req:
            self.assertEqual(self.browser.root_record_path("res.partner", self.gaston.id)["rel_path"], rel_client)
            self.assertEqual(
                self.browser.root_record_path("project.project", project.id)["rel_path"],
                rel_client + "/(PROJETS)/001 - P",
            )
            self.assertEqual(
                self.browser.root_record_path("project.task", task.id)["rel_path"],
                rel_client + "/(PROJETS)/001 - P",
            )
            # Any record with a partner: here a user whose partner works at Valberge.
            colleague = self.env["res.users"].with_context(no_reset_password=True).create({
                "name": "Chez Valberge", "login": "valberge@dossiers.invalid", "parent_id": self.valberge.id,
            })
            res = self.browser.root_record_path("res.users", colleague.id)
            self.assertEqual(res["rel_path"], rel_client)
        req.assert_not_called()
        self.assertTrue(folder)

    def test_the_panel_says_nothing_for_what_it_cannot_or_must_not_follow(self):
        self._folder(self.valberge, "041 - Toitures Valberge")
        cases = [
            ("res.partner", self.other.id),       # no folder
            ("no.such.model", 1),                  # unknown model
            ("bf.nc.folder.pick", 1),              # transient
            ("res.partner", 0),                    # no record
            ("res.partner", "x"),                  # not an id
        ]
        for model, rid in cases:
            with self.subTest(model=model, rid=rid):
                self.assertEqual(self.browser.root_record_path(model, rid)["rel_path"], "")
        with self.assertRaises(AccessError):
            self.env["bf.nc.browser"].with_user(self.carol).root_record_path("res.partner", self.valberge.id)

    def test_the_panel_ignores_a_folder_typed_outside_the_root(self):
        project = self.env["project.project"].create({"name": "P"})
        project.nc_documents_folder = "/Ailleurs/P"
        self.assertEqual(self.browser.root_record_path("project.project", project.id)["rel_path"], "")

    # ------------------------------------------------------------------
    # Choosing the folder
    # ------------------------------------------------------------------
    def _pick(self, record, route):
        with patch("requests.request", side_effect=route):
            action = record.with_user(self.alice).action_nc_pick_folder()
        return self.env["bf.nc.folder.pick"].with_user(self.alice).browse(action["res_id"])

    def test_the_client_folders_come_closest_first_and_a_taken_one_cannot_be_chosen(self):
        self._folder(self.other, "040 - Autre client")
        listing = _multistatus(
            CLIENTS,
            CLIENTS + "/047 - Kymez",
            CLIENTS + "/041 - Toitures Valberge",
            CLIENTS + "/040 - Autre client",
        )

        def route(method, url, **kwargs):
            self.assertEqual(method, "PROPFIND")
            self.assertTrue(url.endswith(quote(CLIENTS)), url)
            return _response(207, listing)

        pick = self._pick(self.valberge, route)
        names = pick.line_ids.mapped("name")
        self.assertEqual(names[0], "041 - Toitures Valberge")
        taken = pick.line_ids.filtered("taken_by")
        self.assertEqual(taken.name, "040 - Autre client")
        with self.assertRaises(UserError):
            taken.action_choose()
        pick.line_ids[0].action_choose()
        self.assertEqual(self.valberge.nc_client_folder_ids.path, CLIENTS + "/041 - Toitures Valberge")
        self.assertFalse(pick.exists(), "the listing does not outlive the choice")

    def test_a_listing_made_with_one_persons_account_is_hidden_from_another(self):
        group = self.env.ref("bf_nextcloud_browser.group_nc_browser_user")
        bob = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Bob", "login": "bob2@dossiers.invalid",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id, group.id])],
        })
        listing = _multistatus(CLIENTS, CLIENTS + "/041 - Toitures Valberge")
        pick = self._pick(self.valberge, lambda m, u, **k: _response(207, listing))
        Line = self.env["bf.nc.folder.pick.line"].with_user(bob)
        self.assertFalse(Line.search([("pick_id", "=", pick.id)]))
        self.assertFalse(self.env["bf.nc.folder.pick"].with_user(bob).search([("id", "=", pick.id)]))
        with self.assertRaises(AccessError):
            Line.browse(pick.line_ids.ids).write({"path": CLIENTS + "/Ailleurs"})

    def test_contacts_and_projects_help_rank_a_folder_named_after_a_person(self):
        self.env["project.project"].create({"name": "ASVB - Chantier Sérénité - Phase 2", "partner_id": self.asvb.id})
        listing = _multistatus(
            CLIENTS,
            CLIENTS + "/043 - Anne-Sophie Valmorel Brideaux (perso)",
            CLIENTS + "/009 - Chantier Sérénité",
            CLIENTS + "/047 - Kymez",
        )
        pick = self._pick(self.asvb, lambda m, u, **k: _response(207, listing))
        self.assertEqual(
            set(pick.line_ids[:2].mapped("name")),
            {"043 - Anne-Sophie Valmorel Brideaux (perso)", "009 - Chantier Sérénité"},
        )

    def test_a_member_who_cannot_edit_contacts_still_links_a_client_folder(self):
        group = self.env.ref("bf_nextcloud_browser.group_nc_browser_user")
        bob = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Bob", "login": "bob@dossiers.invalid",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id, group.id,
                                  self.env.ref("project.group_project_user").id])],
        })
        self.assertFalse(bob.has_group("base.group_partner_manager"))
        self.env["bf.nc.user.credential"].sudo().create({
            "user_id": bob.id, "config_id": self.config.id, "state": "connected",
            "nc_login": "bob", "nc_user_id": "bob", "nc_display_name": "Bob", "nc_server": BASE,
            "app_password_encrypted": self.config._encrypt_value("pw-b"),
        })
        listing = _multistatus(CLIENTS, CLIENTS + "/041 - Toitures Valberge",
                               dav="/remote.php/dav/files/bob")
        with patch("requests.request", return_value=_response(207, listing)):
            action = self.valberge.with_user(bob).action_nc_pick_folder()
        pick = self.env["bf.nc.folder.pick"].with_user(bob).browse(action["res_id"])
        pick.line_ids[0].action_choose()
        self.assertEqual(self.valberge.nc_client_folder_ids.name, "041 - Toitures Valberge")
        # A project's folder is written on the project: a project user may not.
        project = self.env["project.project"].create({"name": "P", "partner_id": self.valberge.id})
        with patch("requests.request") as req, self.assertRaises(AccessError):
            project.with_user(bob).action_nc_pick_folder()
        req.assert_not_called()

    def test_a_contact_cannot_be_given_its_own_folder(self):
        # Refused before any request: the message names the company to use.
        with patch("requests.request") as req, self.assertRaises(UserError) as err:
            self.gaston.with_user(self.alice).action_nc_pick_folder()
        req.assert_not_called()
        self.assertIn(self.valberge.display_name, str(err.exception))

    def test_the_project_picks_among_its_clients_folders_and_their_projects(self):
        valberge = self._folder(self.valberge, "041 - Toitures Valberge")
        project = self.env["project.project"].create({"name": "Valberge - Agrandissement hangar", "partner_id": self.valberge.id})
        projets = valberge.path + "/(PROJETS)"
        listing = _multistatus(projets, projets + "/001 - Agrandissement hangar", projets + "/002 - Refonte du site")
        # The line names are written in the language of the person who picks (French here).
        self.alice.lang = "fr_CA"
        pick = self._pick(project, lambda m, u, **k: _response(207, listing))
        best = pick.line_ids[0]
        self.assertEqual(best.name, "001 - Agrandissement hangar")
        self.assertIn("041 - Toitures Valberge (dossier du client)", pick.line_ids.mapped("name"))
        best.action_choose()
        self.assertEqual(project.nc_client_folder_id, valberge)
        self.assertEqual(project.nc_project_subfolder, "(PROJETS)/001 - Agrandissement hangar")

    def test_a_client_folder_without_projects_subfolder_is_still_offered(self):
        self._folder(self.valberge, "041 - Toitures Valberge")
        project = self.env["project.project"].create({"name": "P", "partner_id": self.valberge.id})

        def missing(method, url, **kwargs):
            raise NcPathNotFound("absent")

        pick = self._pick(project, missing)
        self.assertEqual(pick.line_ids.mapped("subfolder"), [False])

    def test_a_person_not_connected_is_told_so_not_shown_an_empty_list(self):
        self._folder(self.valberge, "041 - Toitures Valberge")
        project = self.env["project.project"].create({"name": "P", "partner_id": self.valberge.id})
        self.env["bf.nc.user.credential"].sudo().search([("user_id", "=", self.alice.id)]).unlink()
        with patch("requests.request") as req, self.assertRaises(UserError):
            project.with_user(self.alice).action_nc_pick_folder()
        req.assert_not_called()

    def test_a_project_without_client_folder_says_what_to_do_first(self):
        project = self.env["project.project"].create({"name": "P", "partner_id": self.other.id})
        with self.assertRaises(UserError):
            project.with_user(self.alice).action_nc_pick_folder()

    # ------------------------------------------------------------------
    # Settings and matching
    # ------------------------------------------------------------------
    def test_the_clients_root_must_sit_under_the_prefix_and_the_projects_name_be_a_name(self):
        with self.assertRaises(ValidationError):
            self.config.nc_clients_root = "/Ailleurs/(Clients)"
        with self.assertRaises(ValidationError):
            self.config.nc_projects_subfolder = "a/b"

    def test_similarity_ignores_numbers_accents_and_legal_words(self):
        self.assertGreater(nc_match.similarity("041 - Toitures Valberge", "Toitures Valberge inc."), 0.9)
        self.assertGreater(nc_match.similarity("009 - Chantier Sérénité", "ASVB - Chantier Serenite - Phase 2"), 0.9)
        self.assertLess(nc_match.similarity("047 - Kymez", "Toitures Valberge inc."), 0.4)
        # One word held whole: enough for a client's name, not for a project's.
        self.assertGreater(nc_match.similarity("051 - KORVA", "Services KORVA", loose=True), 0.9)
        self.assertLess(nc_match.similarity("051 - KORVA", "Services KORVA"), 0.6)
        self.assertLess(nc_match.similarity("055 - Odoo", "Odoo Quincaillerie Lune"), 0.5)
