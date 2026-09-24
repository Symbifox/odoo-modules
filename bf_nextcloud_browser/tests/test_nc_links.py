"""Search, links, shares and chatter links (18.0.4.1.0).

No network: `requests` is patched and the Fernet key too, as in test_nc_person.
What the patched answers look like was measured on Nextcloud 34.0.4 first
(scope sent unencoded, backslash escapes honoured, fileid searchable).
"""

import time
from unittest.mock import MagicMock, patch
from xml.sax.saxutils import escape as xml_escape

from cryptography.fernet import Fernet
from markupsafe import Markup

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_nextcloud_browser.models import bf_nc_browser as browser_mod
from odoo.addons.bf_nextcloud_browser.models.bf_nc_browser import link_re
from odoo.addons.bf_nextcloud_browser.models.nc_identity import NcNotConnected, NcPersonAuth
from odoo.addons.bf_nextcloud_browser.models.nextcloud_document_config import like_literal

KEY = Fernet.generate_key()
BASE = "https://nc.example.test"
ALIAS = "cloud.autre.test"


def _response(status, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.json.return_value = json_data if json_data is not None else {}
    return resp


def _multistatus(*items):
    """A DAV multistatus: (href, is_dir, fileid, size)."""
    parts = []
    for href, is_dir, fid, size in items:
        rtype = "<d:collection/>" if is_dir else ""
        parts.append(
            "<d:response><d:href>%s</d:href><d:propstat><d:prop>"
            "<d:resourcetype>%s</d:resourcetype><oc:fileid>%d</oc:fileid><oc:size>%d</oc:size>"
            "<d:getlastmodified>Mon, 14 Sep 2026 12:00:00 GMT</d:getlastmodified>"
            "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
            % (href, rtype, fid, size)
        )
    return (
        '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
        + "".join(parts)
        + "</d:multistatus>"
    )


def _share(sid, path, token="", share_type=3, expiration=None, permissions=17, password=None, **extra):
    return {
        "id": str(sid), "share_type": share_type, "token": token,
        "url": (BASE + "/s/" + token) if token else "", "path": path,
        "item_type": "file", "permissions": permissions, "expiration": expiration,
        "password": password, "uid_owner": "alice@corp", "uid_file_owner": "alice@corp",
        "displayname_owner": "Alice",
        "stime": 1757851200, **extra,
    }


def _ocs(data):
    return _response(200, {"ocs": {"meta": {"status": "ok"}, "data": data}})


@tagged("post_install", "-at_install")
class TestNcLinks(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Config = cls.env["nextcloud.document.config"]
        cls._key_patch = patch.object(type(Config), "_get_encryption_key", return_value=KEY)
        cls._key_patch.start()
        cls.addClassCleanup(cls._key_patch.stop)

        cls.config = Config.create({
            "name": "Essai",
            "nextcloud_base_url": BASE,
            "webdav_path": "/remote.php/dav/files/",
            "nextcloud_user": "service",
            "nextcloud_app_password": "service-secret",
            "browser_root_prefix": "/Équipe & Cie/",
            "nc_link_hosts": ALIAS + ", https://ancien.example.test/nextcloud; ",
        })
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_document_nextcloud_sync.default_config_id", cls.config.id
        )
        group = cls.env.ref("bf_nextcloud_browser.group_nc_browser_user")
        users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.alice = users.create({
            "name": "Alice", "login": "alice@essai.invalid",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id, group.id])],
        })
        cls.carol = users.create({  # internal user, not in the browser group
            "name": "Carol", "login": "carol@essai.invalid",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
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
        cls.project = cls.env["project.project"].create({"name": "Projet liens"})
        cls.task = cls.env["project.task"].create({"name": "Tâche liens", "project_id": cls.project.id})
        cls.browser = cls.env["bf.nc.browser"].with_user(cls.alice)
        cls.dav = "/remote.php/dav/files/alice@corp/Équipe & Cie"

    def _href(self, rel):
        return (self.dav + "/" + rel).replace(" ", "%20").replace("&", "%26")

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    def test_the_literal_neutralises_wildcards_and_markup(self):
        self.assertEqual(
            like_literal("100%_a\\b<c&"),
            xml_escape("%" + "100\\%\\_a\\\\b<c&" + "%"),
        )

    def test_search_speaks_as_the_person_under_the_root_and_keeps_only_what_is_under_it(self):
        body = _multistatus(
            (self._href(""), True, 1, 0),                          # the root itself
            (self._href("Clients/Devis 2026.pdf"), False, 42, 1200),
            (self._href("Devis"), True, 43, 0),
            ("/remote.php/dav/files/alice@corp/Ailleurs/Devis.pdf", False, 44, 10),
        )
        with patch("requests.request", return_value=_response(207, text=body)) as req:
            res = self.browser.root_search_entries("  Devis  ")
        method, url = req.call_args.args[:2]
        kwargs = req.call_args.kwargs
        self.assertEqual((method, url), ("SEARCH", BASE + "/remote.php/dav/"))
        sent = kwargs["data"].decode("utf-8")
        # Unencoded scope (Nextcloud 34 answers 404 to an encoded one), XML-escaped.
        self.assertIn("<d:href>/files/alice@corp/Équipe &amp; Cie</d:href>", sent)
        self.assertIn("<d:literal>%Devis%</d:literal>", sent)
        self.assertIn("<d:nresults>%d</d:nresults>" % (browser_mod.SEARCH_LIMIT + 1), sent)
        self.assertIsInstance(kwargs["auth"], NcPersonAuth)
        self.assertEqual(kwargs["auth"].username, "alice@corp")

        self.assertEqual([e["rel"] for e in res["entries"]], ["Clients/Devis 2026.pdf", "Devis"])
        pdf, folder = res["entries"]
        self.assertEqual(pdf["parent_rel"], "Clients")
        self.assertEqual(folder["parent_rel"], "")
        self.assertEqual(pdf["link_url"], BASE + "/f/42")
        self.assertEqual(folder["link_url"], BASE + "/f/43")
        self.assertEqual(folder["internal_url"], "")  # the Collabora click stays for files
        self.assertFalse(res["truncated"])

    def test_a_hostile_term_cannot_add_search_criteria(self):
        with patch("requests.request", return_value=_response(207, text=_multistatus())) as req:
            self.browser.root_search_entries("x</d:literal></d:like><d:gt>")
        sent = req.call_args.kwargs["data"].decode("utf-8")
        self.assertEqual(sent.count("<d:like>"), 1)
        self.assertNotIn("<d:gt>", sent)

    def test_a_short_term_asks_nothing(self):
        with patch("requests.request") as req:
            res = self.browser.root_search_entries("a")
        req.assert_not_called()
        self.assertTrue(res["too_short"])

    def test_more_hits_than_shown_is_said(self):
        items = [(self._href("f%d.txt" % i), False, 100 + i, 1) for i in range(browser_mod.SEARCH_LIMIT + 1)]
        with patch("requests.request", return_value=_response(207, text=_multistatus(*items))):
            res = self.browser.root_search_entries("txt")
        self.assertEqual(len(res["entries"]), browser_mod.SEARCH_LIMIT)
        self.assertTrue(res["truncated"])

    def test_search_failures(self):
        with patch("requests.request", return_value=_response(404)):
            self.assertEqual(self.browser.root_search_entries("devis")["entries"], [])
        with patch("requests.request", return_value=_response(500)):
            with self.assertRaises(UserError):
                self.browser.root_search_entries("devis")

    def test_search_needs_the_group_and_a_connection(self):
        with patch("requests.request") as req:
            with self.assertRaises(AccessError):
                self.env["bf.nc.browser"].with_user(self.carol).root_search_entries("devis")
            self.env["bf.nc.user.credential"].sudo().search([("user_id", "=", self.alice.id)]).state = "pending"
            with self.assertRaises(NcNotConnected):
                self.browser.root_search_entries("devis")
        req.assert_not_called()

    def test_record_search_is_bounded_by_the_record_folder(self):
        self.project.write({
            "nc_documents_config_id": self.config.id,
            "nc_documents_folder": "/Équipe & Cie/Projet A/",
        })
        body = _multistatus(
            (self._href("Projet A/plan.pdf"), False, 7, 1),
            (self._href("Projet B/plan.pdf"), False, 8, 1),
        )
        with patch("requests.request", return_value=_response(207, text=body)) as req:
            res = self.browser.search_entries("project.task", self.task.id, "plan")
        self.assertIn("<d:href>/files/alice@corp/Équipe &amp; Cie/Projet A</d:href>",
                      req.call_args.kwargs["data"].decode("utf-8"))
        self.assertEqual([e["rel"] for e in res["entries"]], ["plan.pdf"])

    # ------------------------------------------------------------------
    # Links to paste
    # ------------------------------------------------------------------
    def test_an_internal_link_creates_no_share(self):
        body = _multistatus((self._href("Clients/Devis.pdf"), False, 42, 1))
        with patch("requests.request", return_value=_response(207, text=body)) as req, \
                patch("requests.post") as post:
            res = self.browser.root_make_link("Clients/Devis.pdf", "internal")
        post.assert_not_called()
        self.assertEqual(req.call_args.args[0], "PROPFIND")
        self.assertEqual(res, {"ok": True, "kind": "internal", "name": "Devis.pdf", "url": BASE + "/f/42"})

    def test_a_share_link_uses_the_preset_and_never_the_root(self):
        self.config._ensure_share_presets()
        externe = self.config.share_preset_ids.filtered(lambda p: p.name == "Externe")
        ocs = _response(200, text="<ocs><data><id>9</id><url>%s/s/AbCdEf123456789</url>"
                                  "<token>AbCdEf123456789</token></data></ocs>" % BASE)
        file_prop = _response(207, text=_multistatus((self._href("Devis.pdf"), False, 42, 1)))
        with patch("requests.request", return_value=file_prop), \
                patch("requests.post", return_value=ocs) as post:
            res = self.browser.root_make_link("Devis.pdf", "share", externe.id)
        self.assertEqual(res["kind"], "share")
        self.assertEqual(res["url"], BASE + "/s/AbCdEf123456789")
        self.assertEqual(post.call_args.kwargs["data"]["permissions"], 1)
        self.assertIn("expireDate", post.call_args.kwargs["data"])
        with patch("requests.post") as post:
            with self.assertRaises(UserError):
                self.browser.root_make_link("", "share", externe.id)
            with self.assertRaises(UserError):
                self.browser.root_make_link("Devis.pdf", "public-forever")
        post.assert_not_called()

    def test_presets_describe_the_rules_a_share_really_gets(self):
        """« Interne » says 0 day and no password, and gets 30 days and a password."""
        self.config.write({"default_share_expiry_days": 30, "share_password_enabled": True})
        self.config._ensure_share_presets()
        presets = {p["name"]: p for p in self.browser._preset_payload(self.config.sudo())}
        self.assertEqual((presets["Interne"]["expiry_days"], presets["Interne"]["password_protected"]), (30, True))
        self.config.share_password_enabled = False
        presets = {p["name"]: p for p in self.browser._preset_payload(self.config.sudo())}
        self.assertFalse(presets["Externe"]["password_protected"])

    def test_the_link_hosts_are_parsed(self):
        self.assertEqual(
            self.config._link_hosts(),
            {"nc.example.test", ALIAS, "ancien.example.test"},
        )

    # ------------------------------------------------------------------
    # Shares: state, detail, revocation
    # ------------------------------------------------------------------
    def test_share_states_count_children_under_the_root_only(self):
        shares = [
            _share(1, "/Équipe & Cie/a.pdf", token="tok1111111111"),
            _share(2, "/Équipe & Cie/a.pdf", share_type=0),
            _share(3, "/Autre/b.pdf", token="tok2222222222"),
        ]
        with patch("requests.request", return_value=_ocs(shares)) as req:
            res = self.browser.root_share_states("")
        params = req.call_args.kwargs["params"]
        self.assertEqual((params["path"], params["subfiles"], params["reshares"]),
                         ("/Équipe & Cie", "true", "true"))
        self.assertEqual(res["states"], {"a.pdf": {"public": 1, "other": 1}})

    def test_the_detail_of_a_share_is_read_alone(self):
        listed = [_share(5, "/Équipe & Cie/a.pdf", token="tok5555555555", expiration=None)]
        alone = [_share(5, "/Équipe & Cie/a.pdf", token="tok5555555555", expiration="2026-10-01 00:00:00")]
        with patch("requests.request", side_effect=[_ocs(listed), _ocs(alone)]) as req:
            res = self.browser.root_entry_shares("a.pdf")
        self.assertTrue(req.call_args_list[1].args[1].endswith("/shares/5"))
        share = res["shares"][0]
        self.assertEqual(share["expiration"], "2026-10-01")
        self.assertTrue(share["is_public"] and share["can_revoke"])
        self.assertEqual(share["kind"], "Lien public")

    def test_revocation_stays_inside_the_browser_folder(self):
        outside = [_share(6, "/Autre/b.pdf", token="tok6666666666")]
        with patch("requests.request", return_value=_ocs(outside)) as req:
            with self.assertRaises(UserError):
                self.browser.root_revoke_share(6)
        self.assertEqual([c.args[0] for c in req.call_args_list], ["GET"])

        inside = [_share(7, "/Équipe & Cie/a.pdf", token="tok7777777777")]
        with patch("requests.request", side_effect=[_ocs(inside), _ocs([])]) as req:
            self.assertEqual(self.browser.root_revoke_share("7"), {"ok": True, "gone": False})
        self.assertEqual(req.call_args_list[1].args[0], "DELETE")
        self.assertTrue(req.call_args_list[1].args[1].endswith("/shares/7"))

        with patch("requests.request", return_value=_response(404)) as req:
            self.assertEqual(self.browser.root_revoke_share(8), {"ok": True, "gone": True})
        self.assertEqual(len(req.call_args_list), 1)

        with patch("requests.request") as req:
            with self.assertRaises(UserError):
                self.browser.root_revoke_share("8 OR 1")
            with self.assertRaises(AccessError):
                self.env["bf.nc.browser"].with_user(self.carol).root_revoke_share(7)
        req.assert_not_called()

    # ------------------------------------------------------------------
    # Files linked from a chatter
    # ------------------------------------------------------------------
    def _post(self, record, html):
        return record.message_post(body=Markup(html), message_type="comment", subtype_xmlid="mail.mt_note")

    def test_links_are_read_from_the_chatter_on_every_known_host(self):
        self._post(self.task, '<p>Plan : <a href="%s/f/42">%s/f/42</a></p>' % (BASE, BASE))
        self._post(self.task, "<p>Devis https://%s/index.php/s/ShareTok12345 et https://ancien.example.test/nextcloud/f/77</p>" % ALIAS)
        self._post(self.task, "<p>Ailleurs : https://evil.example.test/f/99 et %s/f/notanid</p>" % BASE)
        # Sans schema (forme reelle dans les chatters), et un sondage qui n'est pas un partage.
        self._post(self.task, "<p>Note : nc.example.test/f/78 et %s/apps/polls/s/PollTok12345</p>" % BASE)
        self._post(self.task, "<p>Faux hote : xnc.example.test/f/79 et mail@nc.example.test/f/80</p>")
        other = self.env["project.task"].create({"name": "Autre", "project_id": self.project.id})
        self._post(other, "<p>%s/f/555</p>" % BASE)

        found = _multistatus(
            (self._href("Clients/Plan.pdf"), False, 42, 5),
            ("/remote.php/dav/files/alice@corp/Notes/idee.md", False, 78, 5),
        )
        share_list = [_share(11, "/Documents/Devis.pdf", token="ShareTok12345")]
        share_alone = [_share(11, "/Documents/Devis.pdf", token="ShareTok12345", expiration="2026-12-31")]

        def route(method, url, **kwargs):
            if method == "SEARCH":
                return _response(207, text=found)
            if url.endswith("/shares/11"):
                return _ocs(share_alone)
            return _ocs(share_list)

        with patch("requests.request", side_effect=route) as req:
            res = self.browser.linked_files("project.task", self.task.id)

        search = [c for c in req.call_args_list if c.args[0] == "SEARCH"]
        self.assertEqual(len(search), 1)
        sent = search[0].kwargs["data"].decode("utf-8")
        self.assertIn("<d:literal>42</d:literal>", sent)
        self.assertIn("<d:literal>77</d:literal>", sent)
        self.assertIn("<d:href>/files/alice@corp</d:href>", sent)  # the whole account
        self.assertNotIn("99", sent)
        self.assertNotIn("555", sent)

        links = {(l["kind"], l["key"]): l for l in res["links"]}
        self.assertEqual(
            set(links),
            {("internal", "42"), ("share", "ShareTok12345"), ("internal", "77"), ("internal", "78")},
        )
        self.assertEqual(links[("internal", "42")]["mentions"], 1)  # href and text: one mention
        self.assertEqual(links[("internal", "42")]["entry"]["rel"], "Équipe & Cie/Clients/Plan.pdf")
        self.assertTrue(links[("internal", "42")]["in_browser"])
        self.assertEqual(links[("internal", "78")]["entry"]["rel"], "Notes/idee.md")
        self.assertFalse(links[("internal", "78")]["in_browser"])
        self.assertFalse(links[("internal", "77")]["found"])
        share = links[("share", "ShareTok12345")]["share"]
        self.assertEqual((share["expiration"], share["can_revoke"]), ("2026-12-31", True))
        self.assertFalse(links[("share", "ShareTok12345")]["in_browser"])

    def test_a_linked_share_is_revoked_only_if_the_thread_cites_it(self):
        self._post(self.task, "<p>%s/s/ShareTok12345</p>" % BASE)
        cited = [_share(11, "/Documents/Devis.pdf", token="ShareTok12345")]
        other = [_share(12, "/Documents/Autre.pdf", token="OtherTok12345")]
        with patch("requests.request", return_value=_ocs(other)) as req:
            with self.assertRaises(UserError):
                self.browser.linked_revoke_share("project.task", self.task.id, 12)
        self.assertEqual([c.args[0] for c in req.call_args_list], ["GET"])
        with patch("requests.request", side_effect=[_ocs(cited), _ocs([])]) as req:
            self.assertEqual(
                self.browser.linked_revoke_share("project.task", self.task.id, 11),
                {"ok": True, "gone": False},
            )
        self.assertEqual(req.call_args_list[1].args[0], "DELETE")

    def test_linked_files_follow_the_record_rights(self):
        secret = self.env["project.project"].create({"name": "Secret", "privacy_visibility": "followers"})
        hidden = self.env["project.task"].create({"name": "Cachée", "project_id": secret.id})
        self._post(hidden, "<p>%s/f/42</p>" % BASE)
        with patch("requests.request") as req:
            with self.assertRaises(AccessError):
                self.browser.linked_files("project.task", hidden.id)
            with self.assertRaises(AccessError):
                self.browser.linked_revoke_share("project.task", hidden.id, 11)
            with self.assertRaises(ValidationError):
                self.browser.linked_files("ir.config_parameter", 1)
            with self.assertRaises(ValidationError):
                self.browser.linked_files("no.such.model", 1)
            with self.assertRaises(AccessError):
                self.env["bf.nc.browser"].with_user(self.carol).linked_files("project.task", self.task.id)
        req.assert_not_called()

    # ------------------------------------------------------------------
    # Adversarial review of 18.0.4.1.0
    # ------------------------------------------------------------------
    def test_a_path_or_name_that_still_decodes_is_refused_before_any_call(self):
        """`%252e%252e` passed the check as `%2e%2e` and reached Nextcloud as `..`."""
        self.project.write({
            "nc_documents_config_id": self.config.id,
            "nc_documents_folder": "/Équipe & Cie/Projet A/",
        })
        with patch("requests.request") as req, patch("requests.post") as post:
            for call in (
                lambda: self.browser.root_make_link("%252e%252e/Ailleurs/Devis.pdf", "internal"),
                lambda: self.browser.root_make_link("%252e", "share"),
                lambda: self.browser.delete_entry("project.project", self.project.id, "%252e%252e/%252e%252e"),
                lambda: self.browser.root_rename_entry("Devis.pdf", "%252e%252e"),
                lambda: self.browser.root_make_folder("", "%252e%252e"),
                lambda: self.browser.root_upload_file("", "%2e%2e", "eA=="),
                lambda: self.browser.root_browse_dir("Clients%2F..%2F..%2FAilleurs"),
            ):
                with self.assertRaises(UserError):
                    call()
            # Depuis bf_document_nextcloud_sync 18.0.1.5.0, le socle refuse un
            # dossier qui se decoderait des l'ecriture : le defaut est arrete
            # encore plus tot, et le garde du navigateur reste le second rempart.
            with self.assertRaises(ValidationError):
                self.project.nc_documents_folder = "/Équipe & Cie/Projet%2520A/"
        req.assert_not_called()
        post.assert_not_called()

    def test_a_percent_that_does_not_decode_is_a_normal_name(self):
        body = _multistatus((self._href("Facture 100%25.txt"), False, 5, 1))
        with patch("requests.request", return_value=_response(207, text=body)) as req:
            res = self.browser.root_make_link("Facture 100%.txt", "internal")
        self.assertEqual(req.call_args.args[0], "PROPFIND")
        self.assertEqual(res["url"], BASE + "/f/5")

    def test_search_terms_are_strings_of_xml_characters(self):
        with patch("requests.request") as req:
            with self.assertRaises(UserError):
                self.browser.root_search_entries(["devis"])
        req.assert_not_called()
        with patch("requests.request", return_value=_response(207, text=_multistatus())) as req:
            self.browser.root_search_entries("ab\ud800c\x0bd")
        sent = req.call_args.kwargs["data"].decode("utf-8")
        self.assertIn("<d:literal>%abcd%</d:literal>", sent)

    def test_abstract_models_are_refused(self):
        with patch("requests.request") as req:
            for model in ("mail.thread", "mail.thread.cc"):
                if model in self.env:
                    with self.assertRaises(ValidationError):
                        self.browser.linked_files(model, 1)
        req.assert_not_called()

    def test_a_path_or_a_name_that_is_not_text_is_refused(self):
        with patch("requests.request") as req:
            for call in (
                lambda: self.browser.root_browse_dir(["Clients"]),
                lambda: self.browser.root_make_folder("", {"x": 1}),
                lambda: self.browser.root_rename_entry("a.pdf", 7),
                lambda: self.browser.root_upload_file("", ["a.pdf"], "eA=="),
                lambda: self.browser.root_search_entries({"a": 1}),
            ):
                with self.assertRaises(UserError):
                    call()
        req.assert_not_called()

    def test_the_link_pattern_is_bounded_and_precise(self):
        pattern = link_re(("nc.example.test",))
        found = lambda body: [(kind, key) for _h, prefix, kind, key in pattern.findall(body)
                              if not set(prefix.lower().split("/")) & browser_mod.NOT_A_LINK_PREFIX]
        self.assertEqual(found("nc.example.test/s/devis-acme-2026"), [("s", "devis-acme-2026")])
        self.assertEqual(found("https://evil.test/?u=nc.example.test/f/44"), [])
        self.assertEqual(found("https://nc.example.test/remote.php/dav/files/a/Docs/s/Contrat2026.pdf"), [])
        self.assertEqual(found("https://nc.example.test/apps/polls/s/PollTok12345"), [])
        hostile = "nc.example.test/a," * 20000  # 360 KB: 8 s per 100 KB unbounded
        start = time.perf_counter()
        pattern.findall(hostile)
        self.assertLess(time.perf_counter() - start, 1.5)

    def test_only_the_persons_own_shares_are_revoked_from_odoo(self):
        """Nextcloud also lets a team-folder resharer delete a colleague's link: Odoo does not."""
        theirs = [_share(21, "/Équipe & Cie/a.pdf", token="Theirs1234567", uid_owner="bob", uid_file_owner="")]
        with patch("requests.request", return_value=_ocs(theirs)) as req:
            with self.assertRaises(UserError):
                self.browser.root_revoke_share(21)
            shares = self.browser.root_entry_shares("a.pdf")["shares"]
        self.assertFalse(shares[0]["can_revoke"])
        self.assertNotIn("DELETE", [c.args[0] for c in req.call_args_list])

        self._post(self.task, "<p>%s/s/Theirs1234567</p>" % BASE)
        with patch("requests.request", return_value=_ocs(theirs)) as req:
            with self.assertRaises(UserError):
                self.browser.linked_revoke_share("project.task", self.task.id, 21)
        self.assertNotIn("DELETE", [c.args[0] for c in req.call_args_list])

    def test_share_rereads_are_capped(self):
        tokens = ["Tok%010d" % i for i in range(30)]
        self._post(self.task, "<p>%s</p>" % " ".join("%s/s/%s" % (BASE, t) for t in tokens))
        listed = [_share(100 + i, "/Documents/f%d.pdf" % i, token=t) for i, t in enumerate(tokens)]
        with patch("requests.request", return_value=_ocs(listed)) as req:
            res = self.browser.linked_files("project.task", self.task.id)
        self.assertEqual(len([l for l in res["links"] if l["found"]]), 30)
        alone = [c for c in req.call_args_list if "/shares/" in c.args[1]]
        self.assertEqual(len(alone), browser_mod.SHARE_REREAD_MAX)
        # Past the cap the expiry comes from the list, which omits it: the screen
        # must say "not checked", never "no expiry".
        checked = [l["share"]["expiry_checked"] for l in res["links"]]
        self.assertEqual((checked.count(True), checked.count(False)), (browser_mod.SHARE_REREAD_MAX, 10))

    # ------------------------------------------------------------------
    # The tab of an unmapped record
    # ------------------------------------------------------------------
    def test_the_tab_hides_without_a_folder_for_a_member_who_is_not_administrator(self):
        for Model in (self.env["project.project"], self.env["project.task"]):
            view = Model.with_user(self.alice).get_views([(False, "form")])
            arch = view["views"]["form"]["arch"]
            self.assertIn('name="nc_browser"', arch)
            self.assertIn('invisible="not nc_documents_folder"', arch)
            self.assertIn("nc_documents_folder", view["models"][Model._name]["fields"])
