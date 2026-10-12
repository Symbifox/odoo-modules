"""Creating a client folder from the template, filing chatter attachments into
it (18.0.4.3.0).

No network: `requests` is patched, as in test_nc_client_folder.
"""

import base64
import unicodedata
from unittest.mock import MagicMock, patch
from urllib.parse import quote

from psycopg2 import IntegrityError

from cryptography.fernet import Fernet

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.bf_nextcloud_browser.models import bf_nc_browser as browser_mod
from odoo.addons.bf_nextcloud_browser.models import nc_names

KEY = Fernet.generate_key()
BASE = "https://nc.example.test"
PREFIX = "/Équipe/"
CLIENTS = "/Équipe/(Clients)"
TEMPLATE = CLIENTS + "/(DOSSIER TYPE)"
DAV = "/remote.php/dav/files/alice@corp"


def _response(status, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.json.return_value = {}
    return resp


def _listing(folder, dirs=(), files=()):
    """PROPFIND depth 1 of `folder`: itself, child folders, child files."""
    items = [(folder, True)] + [(folder + "/" + d, True) for d in dirs] + [(folder + "/" + f, False) for f in files]
    parts = []
    for i, (path, is_dir) in enumerate(items):
        href = quote(DAV + path + ("/" if is_dir else ""))
        rtype = "<d:collection/>" if is_dir else ""
        parts.append(
            "<d:response><d:href>%s</d:href><d:propstat><d:prop>"
            "<d:resourcetype>%s</d:resourcetype><oc:fileid>%d</oc:fileid><oc:size>1</oc:size>"
            "<d:getlastmodified>Mon, 14 Sep 2026 12:00:00 GMT</d:getlastmodified>"
            "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>" % (href, rtype, 500 + i)
        )
    return ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
            + "".join(parts) + "</d:multistatus>")


@tagged("post_install", "-at_install")
class TestNcClientFolderLot2(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Config = cls.env["nextcloud.document.config"]
        cls._key_patch = patch.object(type(Config), "_get_encryption_key", return_value=KEY)
        cls._key_patch.start()
        cls.addClassCleanup(cls._key_patch.stop)
        cls.config = Config.create({
            "name": "Essai lot 2",
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
        users = cls.env["res.users"].with_context(no_reset_password=True)
        base = [cls.env.ref("base.group_user").id, cls.env.ref("project.group_project_manager").id]
        cls.alice = users.create({"name": "Alice", "login": "alice@lot2.invalid", "email": "alice@lot2.invalid",
                                  "groups_id": [(6, 0, base + [group.id])]})
        # A member without email address: message_post refuses such an author.
        cls.bob = users.create({"name": "Bob", "login": "bob-lot2",
                                "groups_id": [(6, 0, base + [group.id])]})
        cls.carol = users.create({"name": "Carol", "login": "carol@lot2.invalid",
                                  "groups_id": [(6, 0, base)]})
        for user, login in ((cls.alice, "alice@corp"), (cls.bob, "bob")):
            cls.env["bf.nc.user.credential"].sudo().create({
                "user_id": user.id, "config_id": cls.config.id, "state": "connected",
                "nc_login": login, "nc_user_id": login, "nc_display_name": user.name,
                "nc_server": BASE, "app_password_encrypted": cls.config._encrypt_value("pw"),
            })
        Partner = cls.env["res.partner"]
        cls.valberge = Partner.create({"name": "Toitures Valberge inc.", "is_company": True})
        cls.gaston = Partner.create({"name": "Gaston Vérillac", "parent_id": cls.valberge.id})
        cls.root_listing = _listing(CLIENTS, dirs=(
            "(DOSSIER TYPE)", "000 - Prospects avec documents", "009 - Chantier Sérénité", "029 - ZOLTA",
        ))

    def _route(self, copy_status=201, listings=None):
        calls = []
        listings = listings or {}

        def route(method, url, **kwargs):
            calls.append((method, url, kwargs))
            if method == "PROPFIND":
                for path, body in listings.items():
                    if url.endswith(quote(path)) or url.endswith(quote(path) + "/"):
                        return _response(207, body)
                return _response(207, self.root_listing)
            if method == "COPY":
                return _response(copy_status)
            return _response(500)
        return route, calls

    # ------------------------------------------------------------------
    # Creating the folder
    # ------------------------------------------------------------------
    def test_names_are_numbered_after_the_highest_and_cleaned_for_windows(self):
        self.assertEqual(nc_names.next_number(["029 - ZOLTA", "(DOSSIER TYPE)", "000 - P"]), (30, 3))
        self.assertEqual(nc_names.build_name("{NNN} - {client}", 30, 3, 'A/B: "C"?.'), "030 - A B C")
        self.assertEqual(nc_names.build_name("{N} - {client}", 7, 0, "X"), "7 - X")
        self.assertEqual(nc_names.next_number([]), (1, 0))

    def test_the_folder_is_copied_from_the_template_with_the_next_number_and_linked(self):
        # The note on the company needs the right to write there (contacts).
        self.alice.groups_id = [(4, self.env.ref("base.group_partner_manager").id)]
        route, calls = self._route()
        with patch("requests.request", side_effect=route):
            path = self.valberge.with_user(self.alice).nc_create_client_folder()
        self.assertEqual(path, CLIENTS + "/030 - Toitures Valberge inc")
        copies = [c for c in calls if c[0] == "COPY"]
        self.assertEqual(len(copies), 1)
        _m, url, kw = copies[0]
        self.assertTrue(url.endswith(quote(TEMPLATE)), url)
        self.assertTrue(kw["headers"]["Destination"].endswith(quote(path)), kw["headers"])
        self.assertEqual((kw["headers"]["Overwrite"], kw["headers"]["Depth"]), ("F", "infinity"))
        self.assertEqual(kw["auth"].username, "alice@corp")
        self.assertEqual(self.valberge.nc_client_folder_ids.path, path)
        self.assertIn(path, self.valberge.message_ids[:1].body)

    def test_without_an_email_address_the_work_is_done_and_only_the_note_is_skipped(self):
        self.assertFalse(self.bob.email)
        bob_listing = self.root_listing.replace(quote(DAV), quote("/remote.php/dav/files/bob"))
        self.root_listing, saved = bob_listing, self.root_listing
        try:
            route, calls = self._route()
            with patch("requests.request", side_effect=route):
                path = self.valberge.with_user(self.bob).nc_create_client_folder()
        finally:
            self.root_listing = saved
        self.assertEqual(self.valberge.nc_client_folder_ids.path, path)  # linked despite the refused note
        self.assertEqual(len([c for c in calls if c[0] == "COPY"]), 1)

    def test_the_wizard_proposes_the_name_and_the_person_may_change_it(self):
        route, calls = self._route()
        with patch("requests.request", side_effect=route):
            action = self.valberge.with_user(self.alice).action_nc_create_client_folder()
            wizard = self.env["bf.nc.client.folder.create"].with_user(self.alice).browse(action["res_id"])
            self.assertEqual(wizard.name, "030 - Toitures Valberge inc")
            wizard.name = "030 - Valberge"
            wizard.action_create()
        self.assertEqual(self.valberge.nc_client_folder_ids.name, "030 - Valberge")
        self.assertFalse(wizard.exists())
        # Alice may not write on companies here: no note, the folder is linked all the same.
        self.assertNotIn("030 - Valberge", self.valberge.message_ids[:1].body or "")

    def test_creation_refuses_a_taken_name_a_contact_a_missing_template_and_a_412(self):
        route, calls = self._route()
        with patch("requests.request", side_effect=route):
            with self.assertRaises(UserError):
                self.valberge.with_user(self.alice).nc_create_client_folder("029 - zolta")  # case and form
            with self.assertRaises(UserError):
                self.gaston.with_user(self.alice).nc_create_client_folder()
        self.assertFalse([c for c in calls if c[0] == "COPY"])
        route, calls = self._route(copy_status=412)
        with patch("requests.request", side_effect=route), self.assertRaises(UserError):
            self.valberge.with_user(self.alice).nc_create_client_folder()
        self.assertFalse(self.valberge.nc_client_folder_ids)
        self.config.nc_client_template = False
        with patch("requests.request") as req, self.assertRaises(UserError):
            self.valberge.with_user(self.alice).nc_create_client_folder()
        req.assert_not_called()

    def test_a_person_outside_the_group_cannot_create(self):
        with patch("requests.request") as req, self.assertRaises(AccessError):
            self.valberge.with_user(self.carol).nc_create_client_folder()
        req.assert_not_called()

    # ------------------------------------------------------------------
    # Filing chatter attachments
    # ------------------------------------------------------------------
    def _attach(self, record, name, data=b"contenu"):
        att = self.env["ir.attachment"].create({
            "name": name, "datas": base64.b64encode(data), "res_model": record._name, "res_id": record.id,
        })
        record.message_post(body="pièce", attachment_ids=att.ids)
        return att

    def _setup_task(self):
        folder = self.env["bf.nc.client.folder"].create({"partner_id": self.valberge.id, "path": CLIENTS + "/041 - Valberge"})
        project = self.env["project.project"].create({"name": "P", "partner_id": self.valberge.id})
        task = self.env["project.task"].create({"name": "T", "project_id": project.id})
        return folder, task

    def test_the_thread_lists_its_attachments_newest_first(self):
        _f, task = self._setup_task()
        a = self._attach(task, "Entente.pdf")
        b = self._attach(task, "Devis.pdf")
        other = self.env["project.task"].create({"name": "Autre", "project_id": task.project_id.id})
        self._attach(other, "Ailleurs.pdf")
        res = self.env["bf.nc.browser"].with_user(self.alice).thread_attachments("project.task", task.id)
        self.assertEqual([x["id"] for x in res["attachments"]], [b.id, a.id])

    def test_filing_copies_under_a_free_name_keeps_the_attachment_and_leaves_a_note(self):
        folder, task = self._setup_task()
        a = self._attach(task, "Devis.pdf", b"AAA")
        b = self._attach(task, "devis.pdf", b"BBB")
        dest = folder.path + "/Soumissions"
        puts = []

        def route(method, url, **kwargs):
            if method == "PROPFIND":
                return _response(207, _listing(dest, files=("Devis.pdf",)))
            return _response(500)

        self._put_headers = []

        def put(url, data=None, **kwargs):
            puts.append((url, data))
            self._put_headers.append(kwargs.get("headers") or {})
            return _response(201)

        with patch("requests.request", side_effect=route), patch("requests.put", side_effect=put):
            res = self.env["bf.nc.browser"].with_user(self.alice).root_file_attachments(
                "project.task", task.id, [a.id, b.id], "(Clients)/041 - Valberge/Soumissions")
        self.assertEqual(res["done"], ["Devis (2).pdf", "devis (3).pdf"])
        self.assertTrue(puts[0][0].endswith(quote(dest + "/Devis (2).pdf")))
        self.assertEqual(self._put_headers[0].get("If-None-Match"), "*")
        self.assertEqual(puts[0][1], b"AAA")
        self.assertTrue(a.exists() and b.exists())
        self.assertIn("Devis (2).pdf", task.message_ids[:1].body)

    def test_filing_refuses_an_attachment_of_another_thread_and_a_file_as_destination(self):
        folder, task = self._setup_task()
        other = self.env["project.task"].create({"name": "Autre", "project_id": task.project_id.id})
        foreign = self._attach(other, "Ailleurs.pdf")
        mine = self._attach(task, "Ici.pdf")
        Browser = self.env["bf.nc.browser"].with_user(self.alice)
        with patch("requests.request", return_value=_response(207, _listing(folder.path))), \
                patch("requests.put") as put:
            with self.assertRaises(UserError):
                Browser.root_file_attachments("project.task", task.id, [foreign.id], "(Clients)/041 - Valberge")
            with self.assertRaises(UserError):
                Browser.root_file_attachments("project.task", task.id, [], "(Clients)/041 - Valberge")
        file_listing = _listing(folder.path + "/x.pdf").replace("<d:collection/>", "")
        with patch("requests.request", return_value=_response(207, file_listing)), \
                patch("requests.put") as put2, self.assertRaises(UserError):
            Browser.root_file_attachments("project.task", task.id, [mine.id], "(Clients)/041 - Valberge/x.pdf")
        with patch("requests.request") as req, patch("requests.put") as put3, self.assertRaises(Exception):
            Browser.root_file_attachments("project.task", task.id, [mine.id], "../../Ailleurs")
        put.assert_not_called()
        put2.assert_not_called()
        put3.assert_not_called()

    def test_a_too_big_attachment_stays_in_odoo_and_is_named(self):
        folder, task = self._setup_task()
        big = self._attach(task, "Gros.zip", b"0123456789")
        small = self._attach(task, "Petit.txt", b"1")
        with patch.object(browser_mod, "MAX_UPLOAD_BYTES", 5), \
                patch("requests.request", return_value=_response(207, _listing(folder.path))), \
                patch("requests.put", return_value=_response(201)) as put:
            res = self.env["bf.nc.browser"].with_user(self.alice).root_file_attachments(
                "project.task", task.id, [big.id, small.id], "(Clients)/041 - Valberge")
        self.assertEqual((res["done"], res["skipped"]), (["Petit.txt"], ["Gros.zip"]))
        self.assertEqual(put.call_count, 1)

    def test_a_person_outside_the_group_cannot_list_nor_file(self):
        _f, task = self._setup_task()
        att = self._attach(task, "Ici.pdf")
        Browser = self.env["bf.nc.browser"].with_user(self.carol)
        with patch("requests.request") as req, patch("requests.put") as put:
            with self.assertRaises(AccessError):
                Browser.thread_attachments("project.task", task.id)
            with self.assertRaises(AccessError):
                Browser.root_file_attachments("project.task", task.id, [att.id], "(Clients)/041 - Valberge")
        req.assert_not_called()
        put.assert_not_called()

    # ------------------------------------------------------------------
    # Adversarial review of 18.0.4.3.0
    # ------------------------------------------------------------------
    def test_a_name_taken_unseen_by_the_listing_is_left_alone(self):
        # Listing cut at 500, or another person filing the same name meanwhile:
        # the server answers 412 to If-None-Match, the next free name is taken.
        folder, task = self._setup_task()
        att = self._attach(task, "Facture.pdf", b"F")
        answers = [_response(412), _response(201)]
        with patch("requests.request", return_value=_response(207, _listing(folder.path))), \
                patch("requests.put", side_effect=lambda *a, **k: answers.pop(0)) as put:
            res = self.env["bf.nc.browser"].with_user(self.alice).root_file_attachments(
                "project.task", task.id, [att.id], "(Clients)/041 - Valberge")
        self.assertEqual(res["done"], ["Facture (2).pdf"])
        self.assertEqual(put.call_count, 2)

    def test_one_refused_piece_does_not_undo_the_others_nor_their_note(self):
        folder, task = self._setup_task()
        atts = [self._attach(task, "P%d.txt" % i, b"x") for i in range(3)]
        answers = [_response(201), _response(507), _response(201)]
        with patch("requests.request", return_value=_response(207, _listing(folder.path))), \
                patch("requests.put", side_effect=lambda *a, **k: answers.pop(0)):
            res = self.env["bf.nc.browser"].with_user(self.alice).root_file_attachments(
                "project.task", task.id, [a.id for a in atts], "(Clients)/041 - Valberge")
        self.assertEqual(res["done"], ["P0.txt", "P2.txt"])
        self.assertEqual([f["name"] for f in res["failed"]], ["P1.txt"])
        self.assertIn("P2.txt", task.message_ids[:1].body)

    def test_a_piece_missing_from_the_filestore_is_not_sent_empty(self):
        folder, task = self._setup_task()
        att = self._attach(task, "Perdu.pdf", b"contenu")
        with patch.object(type(self.env["ir.attachment"]), "_file_read", return_value=b""), \
                patch("requests.request", return_value=_response(207, _listing(folder.path))), \
                patch("requests.put") as put:
            att.invalidate_recordset(["raw", "datas"])
            res = self.env["bf.nc.browser"].with_user(self.alice).root_file_attachments(
                "project.task", task.id, [att.id], "(Clients)/041 - Valberge")
        put.assert_not_called()
        self.assertEqual([f["name"] for f in res["failed"]], ["Perdu.pdf"])

    def test_more_pieces_than_a_batch_are_refused_not_cut(self):
        _f, task = self._setup_task()
        with patch("requests.request") as req, patch("requests.put") as put, self.assertRaises(UserError) as err:
            self.env["bf.nc.browser"].with_user(self.alice).root_file_attachments(
                "project.task", task.id, list(range(1, browser_mod.FILE_BATCH_MAX + 2)), "(Clients)/041 - Valberge")
        self.assertIn(str(browser_mod.FILE_BATCH_MAX), str(err.exception))
        req.assert_not_called()
        put.assert_not_called()

    def test_an_unreadable_piece_linked_to_a_message_is_left_out_not_fatal(self):
        _f, task = self._setup_task()
        mine = self._attach(task, "Ici.pdf")
        cron = self.env["ir.cron"].search([], limit=1)
        hidden = self.env["ir.attachment"].create({
            "name": "Secret.pdf", "datas": base64.b64encode(b"s"), "res_model": "ir.cron", "res_id": cron.id,
        })
        task.message_post(body="lien forcé", attachment_ids=hidden.ids)
        self.assertEqual(hidden.res_model, "ir.cron")
        Browser = self.env["bf.nc.browser"].with_user(self.alice)
        listed = [x["id"] for x in Browser.thread_attachments("project.task", task.id)["attachments"]]
        self.assertIn(mine.id, listed)
        self.assertNotIn(hidden.id, listed)
        with patch("requests.request") as req, patch("requests.put") as put, self.assertRaises(UserError):
            Browser.root_file_attachments("project.task", task.id, [hidden.id], "(Clients)/041 - Valberge")
        put.assert_not_called()

    def test_a_retried_creation_never_doubles_a_folder(self):
        route, calls = self._route()
        with patch("requests.request", side_effect=route):
            self.valberge.with_user(self.alice).nc_create_client_folder()
            with self.assertRaises(UserError):  # the client has its folder now
                self.valberge.with_user(self.alice).nc_create_client_folder()
        self.assertEqual(len([c for c in calls if c[0] == "COPY"]), 1)

    def test_a_folder_already_named_after_the_client_is_pointed_at(self):
        self.root_listing, saved = _listing(CLIENTS, dirs=("029 - ZOLTA", "030 - Toitures Valberge inc")), self.root_listing
        try:
            route, calls = self._route()
            with patch("requests.request", side_effect=route), self.assertRaises(UserError) as err:
                self.valberge.with_user(self.alice).nc_create_client_folder()
        finally:
            self.root_listing = saved
        self.assertIn("030 - Toitures Valberge inc", str(err.exception))
        self.assertFalse([c for c in calls if c[0] == "COPY"])

    def test_nothing_is_copied_for_a_link_odoo_refuses(self):
        other = self.env["res.partner"].create({"name": "Autre", "is_company": True})
        self.env["bf.nc.client.folder"].create({"partner_id": other.id, "path": CLIENTS + "/030 - Toitures Valberge inc"})
        route, calls = self._route()
        with patch("requests.request", side_effect=route), mute_logger("odoo.sql_db"), \
                self.assertRaises(IntegrityError), self.cr.savepoint():
            self.valberge.with_user(self.alice).nc_create_client_folder()
        self.assertFalse([c for c in calls if c[0] == "COPY"])

    def test_the_template_is_checked_when_used(self):
        self.config.nc_client_template = CLIENTS
        with patch("requests.request") as req, self.assertRaises(UserError):
            self.valberge.with_user(self.alice).nc_create_client_folder()
        self.config.nc_client_template = CLIENTS + "/041 - Valberge"
        self.env["bf.nc.client.folder"].create({"partner_id": self.gaston.parent_id.id, "path": CLIENTS + "/041 - Valberge"})
        other = self.env["res.partner"].create({"name": "Neuf", "is_company": True})
        with patch("requests.request") as req2, self.assertRaises(UserError):
            other.with_user(self.alice).nc_create_client_folder()
        req.assert_not_called()
        req2.assert_not_called()
        with self.assertRaises(ValidationError):
            self.config.browser_root_prefix = "/Ailleurs/"

    def test_names_dates_reserved_devices_patterns_and_unicode_forms(self):
        self.assertEqual(nc_names.next_number(["029 - ZOLTA", "2026-01 Archives"], "{NNN} - {client}"), (30, 3))
        self.assertEqual(nc_names.next_number(["A (007)", "B (012)"], "{client} ({NNN})"), (13, 3))
        self.assertEqual(nc_names.clean_name(". Acme"), "Acme")
        self.assertEqual(nc_names.clean_name("CON.txt"), "_CON.txt")
        self.assertEqual(nc_names.clean_name("100%AB"), "100 AB")
        long_name = nc_names.clean_name("x" * 300 + ".pdf")
        self.assertTrue(len(long_name) <= nc_names.MAX_NAME and long_name.endswith(".pdf"))
        nfd = unicodedata.normalize("NFD", "031 - Élan Nordique")
        self.root_listing, saved = _listing(CLIENTS, dirs=(nfd,)), self.root_listing
        try:
            route, calls = self._route()
            with patch("requests.request", side_effect=route), self.assertRaises(UserError):
                self.valberge.with_user(self.alice).nc_create_client_folder("031 - Élan Nordique")
        finally:
            self.root_listing = saved
        with self.assertRaises(UserError):
            self.valberge.with_user(self.alice).nc_create_client_folder(["pas", "un", "nom"])

    def test_any_readable_pattern_finds_the_twin_and_an_unreadable_one_is_refused(self):
        self.assertEqual(nc_names.same_client(["Acme (007)"], "Acme", "{client} ({NNN})"), "Acme (007)")
        self.assertTrue(nc_names.readable("{client} ({NNN})"))
        with self.assertRaises(ValidationError):
            self.config.nc_client_name_pattern = "{NNN} : {client}"

    def test_the_wizard_refuses_at_once_for_a_client_that_has_its_folder(self):
        self.env["bf.nc.client.folder"].create({"partner_id": self.valberge.id, "path": CLIENTS + "/041 - Valberge"})
        route, calls = self._route()
        with patch("requests.request", side_effect=route), self.assertRaises(UserError):
            self.valberge.with_user(self.alice).action_nc_create_client_folder()
        self.assertFalse(self.env["bf.nc.client.folder.create"].search([]))
