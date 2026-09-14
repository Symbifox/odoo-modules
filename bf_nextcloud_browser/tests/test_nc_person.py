"""Per-person Nextcloud identity (18.0.4.0.x).

No test here reaches a network: `requests` is patched where the module calls
it, and the Fernet key is patched too, so the suite runs on a fresh database
without NC_DOC_SYNC_FERNET_KEY. The real Login Flow v2 journey (approval,
revocation, disabled account) was also played against a Nextcloud 34 server
before release.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from cryptography.fernet import Fernet

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.bf_nextcloud_browser.controllers.main import preview_response
from odoo.addons.bf_nextcloud_browser.models import bf_nc_user_credential as cred_mod
from odoo.addons.bf_nextcloud_browser.models.nc_identity import (
    NcAccountUnavailable,
    NcNotConnected,
    NcPathNotFound,
    NcPersonAuth,
    NcTokenRejected,
)

KEY = Fernet.generate_key()
BASE = "https://nc.example.test"
FLOW_TOKEN = "A1b2" * 16
LOGIN_URL = BASE + "/login/v2/flow/" + FLOW_TOKEN
POLL_URL = BASE + "/login/v2/poll"


def _response(status, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.json.return_value = json_data if json_data is not None else {}
    return resp


def _whoami(nc_id="alice-id", email="alice@essai.invalid", display="Alice N."):
    return _response(200, {"ocs": {"data": {"id": nc_id, "display-name": display, "email": email}}})


@tagged("post_install", "-at_install")
class TestNcPerson(TransactionCase):

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
            "browser_root_prefix": "/Equipe/",
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
        cls.bob = users.create({
            "name": "Bob", "login": "bob@essai.invalid",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id, group.id])],
        })
        cls.Cred = cls.env["bf.nc.user.credential"]

    def _connect(self, user, login="alice", nc_id="alice", password="app-pass"):
        return self.Cred.sudo().create({
            "user_id": user.id,
            "config_id": self.config.id,
            "state": "connected",
            "nc_login": login,
            "nc_user_id": nc_id,
            "nc_display_name": login.title(),
            "nc_server": BASE,
            "app_password_encrypted": self.config._encrypt_value(password),
        })

    def _as(self, user):
        """The configuration as the browser hands it to the helpers."""
        return self.config.with_user(user).sudo().with_context(bf_nc_as_user=user.id)

    # ------------------------------------------------------------------
    # The identity switch, and what it leaves alone
    # ------------------------------------------------------------------
    def test_no_context_keeps_the_configuration_account(self):
        """Crons and the upload wizard never set the key: nothing changes for them."""
        self.assertEqual(self.config.webdav_url, BASE + "/remote.php/dav/files/service/")
        self.assertEqual(self.config._get_auth(), ("service", "service-secret"))

    def test_browser_call_without_connection_raises_instead_of_falling_back(self):
        cfg = self._as(self.alice)
        with self.assertRaises(NcNotConnected):
            cfg._get_auth()
        with self.assertRaises(NcNotConnected):
            cfg.webdav_url

    def test_browser_call_speaks_as_the_person(self):
        self._connect(self.alice, login="alice@corp", nc_id="alice@corp", password="pw-a")
        cfg = self._as(self.alice)
        self.assertEqual(cfg.webdav_url, BASE + "/remote.php/dav/files/alice%40corp/")
        auth = cfg._get_auth()
        self.assertIsInstance(auth, NcPersonAuth)
        self.assertEqual((auth.username, auth.password), ("alice@corp", "pw-a"))

    def test_the_context_key_can_only_name_the_caller(self):
        """An RPC client sends its own context: naming someone else is refused."""
        self._connect(self.bob, login="bob", nc_id="bob", password="pw-b")
        forged = self.config.with_user(self.alice).sudo().with_context(bf_nc_as_user=self.bob.id)
        with self.assertRaises(AccessError):
            forged._get_auth()
        with self.assertRaises(AccessError):
            forged.webdav_url

    def test_a_webdav_path_naming_one_account_is_refused_per_person(self):
        self._connect(self.alice)
        self.config.webdav_path = "/remote.php/dav/files/service/"
        with self.assertRaises(UserError):
            self._as(self.alice).webdav_url

    def test_href_mapping_survives_a_quoted_account_id(self):
        self._connect(self.alice, login="alice@corp", nc_id="alice@corp")
        cfg = self._as(self.alice)
        browser = self.env["bf.nc.browser"]
        for href in (
            "/remote.php/dav/files/alice%40corp/Equipe/Projet/",
            "/remote.php/dav/files/alice@corp/Equipe/Projet/",
        ):
            self.assertEqual(browser._href_to_nc_path(cfg, href), "/Equipe/Projet")

    # ------------------------------------------------------------------
    # Status codes turned into precise errors
    # ------------------------------------------------------------------
    def _hook(self, status, method):
        auth = NcPersonAuth("u", "p", {"rejected": "r", "unavailable": "u", "not_found": "n"})
        response = SimpleNamespace(status_code=status, request=SimpleNamespace(method=method))
        return auth._check_status(response)

    def test_status_hook(self):
        with self.assertRaises(NcTokenRejected):
            self._hook(401, "PROPFIND")
        with self.assertRaises(NcAccountUnavailable):
            self._hook(503, "GET")
        with self.assertRaises(NcPathNotFound):
            self._hook(404, "PROPFIND")
        # DELETE treats 404 as "already gone": the hook must not interfere.
        self.assertEqual(self._hook(404, "DELETE").status_code, 404)
        self.assertEqual(self._hook(207, "PROPFIND").status_code, 207)

    def test_errors_are_not_request_exceptions(self):
        """The parent helpers catch requests.RequestException: ours must pass through."""
        for exc in (NcNotConnected, NcTokenRejected, NcAccountUnavailable, NcPathNotFound):
            self.assertTrue(issubclass(exc, UserError))
            self.assertFalse(issubclass(exc, requests.RequestException))

    # ------------------------------------------------------------------
    # Login Flow v2
    # ------------------------------------------------------------------
    def _start_flow(self, user, login=LOGIN_URL, endpoint=POLL_URL):
        answer = {"login": login, "poll": {"token": "POLL-SECRET", "endpoint": endpoint}}
        with patch.object(cred_mod.requests, "post", return_value=_response(200, answer)) as post:
            res = self.env["bf.nc.browser"].with_user(user).root_nc_connect_start()
        return res, post

    def test_flow_start_stores_the_poll_token_encrypted_and_never_returns_it(self):
        res, post = self._start_flow(self.alice)
        self.assertEqual(post.call_args.args[0], BASE + "/index.php/login/v2")
        self.assertIs(post.call_args.kwargs["allow_redirects"], False)
        self.assertEqual(res["login_url"], LOGIN_URL)
        self.assertNotIn("POLL-SECRET", str(res))
        cred = self.Cred._for(self.config, user=self.alice)
        self.assertTrue(cred.flow_started_on)
        self.assertNotIn("POLL-SECRET", cred.flow_poll_token_encrypted)
        self.assertEqual(self.config._decrypt_value(cred.flow_poll_token_encrypted), "POLL-SECRET")

    def test_flow_start_accepts_the_front_controller_form(self):
        res, _post = self._start_flow(
            self.alice,
            login=BASE + "/index.php/login/v2/flow/" + FLOW_TOKEN,
            endpoint=BASE + "/index.php/login/v2/poll",
        )
        self.assertTrue(res["login_url"].startswith(BASE + "/index.php/login/v2/flow/"))

    def test_flow_start_refuses_every_foreign_or_ambiguous_url(self):
        """urlparse and urllib3 disagree on '\\' and '@': such URLs never pass."""
        bad_logins = (
            "https://evil.test/login/v2/flow/" + FLOW_TOKEN,
            "https://evil.test\\@nc.example.test/login/v2/flow/" + FLOW_TOKEN,
            "https://nc.example.test@evil.test/login/v2/flow/" + FLOW_TOKEN,
            "http://nc.example.test/login/v2/flow/" + FLOW_TOKEN,
            "https://nc.example.test.evil.test/login/v2/flow/" + FLOW_TOKEN,
            BASE + "/login/v2/flow/" + FLOW_TOKEN + "/../../x",
            BASE + "/apps/files/",
            None,
            42,
        )
        bad_endpoints = (
            "https://127.0.0.1\\@nc.example.test/login/v2/poll",
            "https://169.254.169.254/login/v2/poll",
            BASE + "/login/v2/poll?next=https://evil.test",
            BASE + ":99999/login/v2/poll",
            BASE + "/other",
            ["not", "a", "string"],
        )
        for login in bad_logins:
            with self.assertRaises(UserError, msg=repr(login)):
                self._start_flow(self.alice, login=login)
        for endpoint in bad_endpoints:
            with self.assertRaises(UserError, msg=repr(endpoint)):
                self._start_flow(self.alice, endpoint=endpoint)

    def test_flow_poll_pending_then_connected(self):
        self._start_flow(self.alice)
        browser = self.env["bf.nc.browser"].with_user(self.alice)
        with patch.object(cred_mod.requests, "post", return_value=_response(404)) as post:
            res = browser.root_nc_connect_poll()
        self.assertTrue(res["pending"])
        self.assertFalse(res["connected"])
        self.assertEqual(post.call_args.args[0], POLL_URL)
        self.assertIs(post.call_args.kwargs["allow_redirects"], False)

        granted = {"server": BASE, "loginName": "alice.login", "appPassword": "APP-SECRET"}
        with patch.object(cred_mod.requests, "post", return_value=_response(200, granted)), \
                patch.object(cred_mod.requests, "get", return_value=_whoami()) as get:
            res = browser.root_nc_connect_poll()
        self.assertIs(get.call_args.kwargs["allow_redirects"], False)
        self.assertTrue(res["connected"])
        self.assertTrue(res["just_connected"])
        self.assertFalse(res["pending"])
        self.assertNotIn("APP-SECRET", str(res))
        cred = self.Cred._for(self.config, user=self.alice)
        self.assertEqual((cred.nc_login, cred.nc_user_id), ("alice.login", "alice-id"))
        self.assertFalse(cred.flow_poll_token_encrypted)
        self.assertEqual(self.config._decrypt_value(cred.app_password_encrypted), "APP-SECRET")

    def test_a_stored_poll_endpoint_is_checked_again_before_use(self):
        self._start_flow(self.alice)
        cred = self.Cred._for(self.config, user=self.alice)
        cred.flow_poll_endpoint = "https://127.0.0.1\\@nc.example.test/login/v2/poll"
        with patch.object(cred_mod.requests, "post") as post:
            with self.assertRaises(UserError):
                self.env["bf.nc.browser"].with_user(self.alice).root_nc_connect_poll()
        post.assert_not_called()

    def _poll_granted(self, user, granted, whoami=None, delete_status=200):
        with patch.object(cred_mod.requests, "post", return_value=_response(200, granted)), \
                patch.object(cred_mod.requests, "get", return_value=whoami or _whoami()), \
                patch.object(cred_mod.requests, "delete", return_value=_response(delete_status)) as delete:
            res = self.env["bf.nc.browser"].with_user(user).root_nc_connect_poll()
        return res, delete

    def test_a_foreign_server_gets_nothing_and_the_token_is_revoked(self):
        self._start_flow(self.alice)
        granted = {"server": "https://evil.test", "loginName": "a", "appPassword": "x"}
        res, delete = self._poll_granted(self.alice, granted)
        self.assertTrue(res["error"])  # a message for the person, not a trace
        self.assertFalse(res["connected"])
        # Revoked on the CONFIGURED server, never on the address handed back.
        self.assertEqual(delete.call_args.args[0], BASE + "/ocs/v2.php/core/apppassword")
        self.assertEqual(delete.call_args.kwargs["auth"], ("a", "x"))
        self.assertFalse(self.Cred._for(self.config, user=self.alice))

    def test_the_approved_account_must_match_the_persons_login(self):
        """A login page forwarded to someone else must not connect the sender."""
        self._start_flow(self.alice)
        granted = {"server": BASE, "loginName": "bob", "appPassword": "BOB-SECRET"}
        res, delete = self._poll_granted(self.alice, granted, whoami=_whoami("bob", "bob@essai.invalid"))
        self.assertTrue(res["error"])
        self.assertFalse(res["connected"])
        self.assertEqual(delete.call_args.kwargs["auth"], ("bob", "BOB-SECRET"))
        self.assertFalse(self.Cred._for(self.config, user=self.alice))

    def test_an_account_without_email_is_refused_unless_the_check_is_off(self):
        self._start_flow(self.alice)
        granted = {"server": BASE, "loginName": "alice", "appPassword": "S"}
        res, _delete = self._poll_granted(self.alice, granted, whoami=_whoami(email=""))
        self.assertTrue(res["error"])

        self.config.nc_require_email_match = False
        self._start_flow(self.alice)
        res, delete = self._poll_granted(self.alice, granted, whoami=_whoami(email=""))
        self.assertTrue(res["connected"])
        delete.assert_not_called()

    def test_whoami_failure_revokes_the_token(self):
        self._start_flow(self.alice)
        granted = {"server": BASE, "loginName": "alice", "appPassword": "S"}
        res, delete = self._poll_granted(self.alice, granted, whoami=_response(500, {}))
        self.assertTrue(res["error"])
        self.assertEqual(delete.call_args.kwargs["auth"], ("alice", "S"))

    def test_reconnecting_revokes_the_old_token_with_the_old_login(self):
        self._connect(self.alice, login="alice@old.invalid", nc_id="alice", password="OLD")
        self._start_flow(self.alice)
        granted = {"server": BASE, "loginName": "alice", "appPassword": "NEW"}
        res, delete = self._poll_granted(self.alice, granted)
        self.assertTrue(res["connected"])
        self.assertEqual(delete.call_args.kwargs["auth"], ("alice@old.invalid", "OLD"))
        self.assertIs(delete.call_args.kwargs["allow_redirects"], False)

    def test_reconnecting_never_sends_a_token_from_another_server(self):
        cred = self._connect(self.alice, password="FROM-OLD-SERVER")
        cred.nc_server = "https://old.example.test"
        self._start_flow(self.alice)
        granted = {"server": BASE, "loginName": "alice", "appPassword": "NEW"}
        res, delete = self._poll_granted(self.alice, granted)
        self.assertTrue(res["connected"])
        delete.assert_not_called()

    def test_flow_expires_after_a_last_poll(self):
        self._start_flow(self.alice)
        cred = self.Cred._for(self.config, user=self.alice)
        cred.flow_started_on = fields.Datetime.now() - cred_mod.FLOW_TTL - timedelta(seconds=5)
        with patch.object(cred_mod.requests, "post", return_value=_response(404)) as post:
            res = self.env["bf.nc.browser"].with_user(self.alice).root_nc_connect_poll()
        post.assert_called_once()
        self.assertTrue(res["expired"])
        self.assertFalse(self.Cred._for(self.config, user=self.alice))

    def test_an_approval_in_the_last_seconds_is_collected_not_orphaned(self):
        self._start_flow(self.alice)
        cred = self.Cred._for(self.config, user=self.alice)
        cred.flow_started_on = fields.Datetime.now() - cred_mod.FLOW_TTL - timedelta(seconds=5)
        granted = {"server": BASE, "loginName": "alice", "appPassword": "LATE"}
        res, delete = self._poll_granted(self.alice, granted)
        self.assertTrue(res["just_connected"])
        delete.assert_not_called()

    def test_the_odoo_email_field_does_not_vouch_for_anyone(self):
        """People may edit their own email: setting a colleague's must not help."""
        self.alice.with_user(self.alice).write({"email": "bob@essai.invalid"})
        self.assertEqual(self.alice.email, "bob@essai.invalid")
        self._start_flow(self.alice)
        granted = {"server": BASE, "loginName": "bob", "appPassword": "BOB-SECRET"}
        res, delete = self._poll_granted(self.alice, granted, whoami=_whoami("bob", "bob@essai.invalid"))
        self.assertTrue(res["error"])
        self.assertFalse(res["connected"])
        self.assertEqual(delete.call_args.kwargs["auth"], ("bob", "BOB-SECRET"))

    def test_a_nextcloud_login_equal_to_the_odoo_login_is_accepted(self):
        self._start_flow(self.alice)
        granted = {"server": BASE, "loginName": "Alice@Essai.invalid ", "appPassword": "S"}
        res, _delete = self._poll_granted(self.alice, granted, whoami=_whoami(email=""))
        self.assertTrue(res["connected"])

    def test_a_login_that_http_basic_cannot_encode_fails_cleanly(self):
        self._start_flow(self.alice)
        granted = {"server": BASE, "loginName": "\u7528\u6237", "appPassword": "S"}
        err = UnicodeEncodeError("latin-1", "x", 0, 1, "ordinal not in range")
        with patch.object(cred_mod.requests, "post", return_value=_response(200, granted)), \
                patch.object(cred_mod.requests, "get", side_effect=err), \
                patch.object(cred_mod.requests, "delete", side_effect=err):
            res = self.env["bf.nc.browser"].with_user(self.alice).root_nc_connect_poll()
        self.assertTrue(res["error"])
        self.assertFalse(self.Cred._for(self.config, user=self.alice))

    # ------------------------------------------------------------------
    # Server binding
    # ------------------------------------------------------------------
    def test_a_token_is_never_sent_to_another_server(self):
        cred = self._connect(self.alice)
        cred.nc_server = "https://other.example.test"
        with self.assertRaises(NcNotConnected):
            self._as(self.alice)._get_auth()

    def test_moving_the_configuration_revokes_on_the_old_server_and_drops(self):
        self._connect(self.alice, password="pw-a")
        with patch.object(cred_mod.requests, "delete", return_value=_response(200)) as delete:
            self.config.nextcloud_base_url = "https://new.example.test"
        self.assertEqual(delete.call_count, 1)
        self.assertEqual(delete.call_args.args[0], BASE + "/ocs/v2.php/core/apppassword")
        self.assertFalse(self.Cred.sudo().search([("config_id", "=", self.config.id)]))

    def test_a_row_deleted_in_the_same_save_is_revoked_on_its_own_server_only(self):
        """URL changed and a row removed from the connection list in one save:
        the row's unlink() runs with the NEW address in cache."""
        cred = self._connect(self.alice, password="pw-a")
        with patch.object(cred_mod.requests, "delete", return_value=_response(200)) as delete:
            self.config.write({
                "nextcloud_base_url": "https://new.example.test",
                "nc_user_credential_ids": [(2, cred.id)],
            })
        urls = [c.args[0] for c in delete.call_args_list]
        self.assertTrue(urls)
        self.assertTrue(all(u.startswith(BASE + "/") for u in urls), urls)
        self.assertFalse(cred.exists())

    def test_a_refused_address_disconnects_nobody(self):
        self._connect(self.alice, password="pw-a")
        with patch.object(cred_mod.requests, "delete", return_value=_response(200)) as delete:
            with self.assertRaises(Exception):
                with self.env.cr.savepoint():
                    self.config.nextcloud_base_url = "ftp://nulle-part"
        delete.assert_not_called()
        self.assertTrue(self.Cred._for(self.config, user=self.alice))

    def test_a_row_from_another_server_is_dropped_without_sending_its_token(self):
        cred = self._connect(self.alice)
        cred.nc_server = "https://other.example.test"
        with patch.object(cred_mod.requests, "delete", return_value=_response(200)) as delete:
            cred.unlink()
        delete.assert_not_called()

    def test_the_configuration_lists_its_connections_for_administrators(self):
        self._connect(self.alice)
        admin = self.env.ref("base.user_admin")
        self.assertEqual(self.config.with_user(admin).nc_user_credential_ids.user_id, self.alice)

    # ------------------------------------------------------------------
    # Hardening
    # ------------------------------------------------------------------
    def test_only_known_types_are_previewed_as_themselves(self):
        text = ("text/plain; charset=utf-8", "inline", True)
        # Active documents, including the XML types a browser renders that no
        # list of dangerous types ever names in full.
        for name in ("a.html", "a.htm", "a.xhtml", "a.svg", "a.xml", "a.atom", "a.rdf",
                     "a.mml", "a.xsl", "a.csv", "a.json"):
            self.assertEqual(preview_response(name, False), text, name)
        self.assertEqual(preview_response("a.png", False), ("image/png", "inline", True))
        self.assertEqual(preview_response("a.txt", False), ("text/plain", "inline", True))
        # The PDF viewer does not run sandboxed; scripts stay forbidden by the CSP.
        self.assertEqual(preview_response("a.pdf", False), ("application/pdf", "inline", False))
        # Unknown or binary: downloaded, never rendered.
        self.assertEqual(preview_response("a.zip", False)[1], "attachment")
        self.assertEqual(preview_response("sans-extension", False)[1], "attachment")
        self.assertEqual(preview_response("a.html", True), ("text/html", "attachment", False))

    def test_the_preview_response_carries_the_sandbox(self):
        from odoo.addons.bf_nextcloud_browser.controllers.main import BfNcBrowserController

        config = MagicMock()
        config._webdav_get.return_value = b"<feed/>"
        made = {}

        def make_response(content, headers=None, status=200):
            made.update(dict(headers))
            return made

        fake_request = SimpleNamespace(make_response=make_response)
        with patch("odoo.addons.bf_nextcloud_browser.controllers.main.request", fake_request):
            BfNcBrowserController()._stream(config, "/Equipe/note.atom", False)
        self.assertEqual(made["Content-Type"], "text/plain; charset=utf-8")
        self.assertTrue(made["Content-Disposition"].startswith("inline;"))
        self.assertTrue(made["Content-Security-Policy"].startswith("sandbox; script-src 'none'"))
        made.clear()
        with patch("odoo.addons.bf_nextcloud_browser.controllers.main.request", fake_request):
            BfNcBrowserController()._stream(config, "/Equipe/a.pdf", False)
        self.assertNotIn("sandbox", made["Content-Security-Policy"])

    def test_a_malformed_id_is_refused_cleanly(self):
        with self.assertRaises(UserError):
            self.env["bf.nc.browser"].with_user(self.alice).browse_dir("project.project", "abc")

    # ------------------------------------------------------------------
    # Revocation
    # ------------------------------------------------------------------
    def test_disconnect_revokes_on_nextcloud(self):
        self._connect(self.alice, password="pw-a")
        with patch.object(cred_mod.requests, "delete", return_value=_response(200)) as delete:
            res = self.env["bf.nc.browser"].with_user(self.alice).root_nc_disconnect()
        self.assertFalse(res["connected"])
        self.assertEqual(delete.call_args.args[0], BASE + "/ocs/v2.php/core/apppassword")
        self.assertEqual(delete.call_args.kwargs["auth"], ("alice", "pw-a"))
        self.assertFalse(self.Cred._for(self.config, user=self.alice))

    def test_a_context_flag_cannot_skip_the_revocation(self):
        self._connect(self.alice, password="pw-a")
        browser = self.env["bf.nc.browser"].with_user(self.alice).with_context(
            bf_nc_revoked_elsewhere=True
        )
        with patch.object(cred_mod.requests, "delete", return_value=_response(200)) as delete:
            browser.root_nc_disconnect()
        self.assertEqual(delete.call_args.kwargs["auth"], ("alice", "pw-a"))

    def test_a_refused_reconnection_keeps_the_previous_one_visible(self):
        self._connect(self.alice, password="OLD")
        self._start_flow(self.alice)
        granted = {"server": BASE, "loginName": "bob", "appPassword": "BOB"}
        res, _delete = self._poll_granted(self.alice, granted, whoami=_whoami("bob", "bob@essai.invalid"))
        self.assertTrue(res["error"])
        self.assertTrue(res["connected"])
        self.assertFalse(res["pending"])

    def test_unreachable_server_does_not_keep_a_person_connected(self):
        self._connect(self.alice)
        with patch.object(cred_mod.requests, "delete", side_effect=requests.ConnectionError()):
            self.env["bf.nc.browser"].with_user(self.alice).root_nc_disconnect()
        self.assertFalse(self.Cred._for(self.config, user=self.alice))

    def test_changing_the_login_ends_the_connections(self):
        self._connect(self.bob, login="bob", nc_id="bob")
        with patch.object(cred_mod.requests, "delete", return_value=_response(200)) as delete:
            self.bob.login = "BOB@essai.invalid"  # same login, other case: kept
        delete.assert_not_called()
        with patch.object(cred_mod.requests, "delete", return_value=_response(200)) as delete:
            self.bob.login = "robert@essai.invalid"
        delete.assert_called_once()
        self.assertFalse(self.Cred.sudo().search([("user_id", "=", self.bob.id)]))

    def test_archiving_the_account_ends_its_connections(self):
        self._connect(self.bob, login="bob", nc_id="bob")
        with patch.object(cred_mod.requests, "delete", return_value=_response(200)) as delete:
            self.bob.active = False
        delete.assert_called_once()
        self.assertFalse(self.Cred.sudo().search([("user_id", "=", self.bob.id)]))

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------
    def test_a_person_reads_only_their_own_row_and_never_the_secret(self):
        self._connect(self.alice)
        self._connect(self.bob, login="bob", nc_id="bob")
        rows = self.Cred.with_user(self.bob).search([])
        self.assertEqual(rows.user_id, self.bob)
        with self.assertRaises(AccessError):
            rows.read(["app_password_encrypted"])
        with self.assertRaises(AccessError):
            self.Cred.with_user(self.bob).create({"user_id": self.bob.id, "config_id": self.config.id})

    def test_nobody_can_revoke_someone_else_through_rpc(self):
        """The refusal must come BEFORE the call to Nextcloud, which no rollback undoes."""
        alice_row = self._connect(self.alice)
        with patch.object(cred_mod.requests, "delete", return_value=_response(200)) as delete:
            with self.assertRaises(AccessError):
                alice_row.with_user(self.bob).unlink()
            with self.assertRaises(AccessError):
                alice_row.with_user(self.alice).unlink()  # own row: through the facade only
        delete.assert_not_called()
        self.assertTrue(alice_row.exists())

    def test_a_member_who_is_not_administrator_can_browse(self):
        """3.7.x raised AccessError here: the configuration was read as the caller."""
        self._connect(self.alice)
        listing = [
            {"href": "/remote.php/dav/files/alice/Equipe/", "is_dir": True, "name": "Equipe"},
        ] + [
            {"href": "/remote.php/dav/files/alice/Equipe/f%d.txt" % i, "is_dir": False,
             "name": "f%d.txt" % i, "size": 3, "file_id": i}
            for i in range(60)
        ]
        Config = type(self.env["nextcloud.document.config"])
        with patch.object(Config, "_webdav_propfind", return_value=listing) as propfind, \
                patch.object(Config, "_nc_person", autospec=True,
                             side_effect=Config._nc_person) as person:
            res = self.env["bf.nc.browser"].with_user(self.alice).root_browse_dir("")
        self.assertEqual(len(res["entries"]), 60)
        self.assertEqual(propfind.call_args.args[0], "/Equipe")
        # One lookup for the listing, not one per entry.
        self.assertLessEqual(person.call_count, 3)

    # ------------------------------------------------------------------
    # Visibility (menu, systray)
    # ------------------------------------------------------------------
    def _alice_sees_the_app(self):
        menu = self.env.ref("bf_nextcloud_browser.menu_nc_browser_root")
        return menu in self.env["ir.ui.menu"].with_user(self.alice).get_user_roots()

    def test_no_button_and_no_app_on_an_unusable_configuration(self):
        browser = self.env["bf.nc.browser"].with_user(self.alice)
        self.assertTrue(browser.get_panel_config().get("available"))
        self.assertTrue(self._alice_sees_the_app())

        self.config.browser_root_prefix = "/"
        self.assertEqual(browser.get_panel_config(), {})
        self.assertFalse(self._alice_sees_the_app())

        self.config.write({"browser_root_prefix": "/Equipe/", "active": False})
        self.assertEqual(browser.get_panel_config(), {})
        self.assertFalse(self._alice_sees_the_app())

    def test_nobody_gets_a_dead_app_and_admins_keep_the_configuration_menu(self):
        """On the instances where the defect was found, every member was an
        administrator: an exception for them would have left it in place."""
        self.config.active = False
        admin = self.env.ref("base.user_admin")
        Menu = self.env["ir.ui.menu"].with_user(admin)
        self.assertNotIn(self.env.ref("bf_nextcloud_browser.menu_nc_browser_root"), Menu.get_user_roots())
        config_menu = self.env.ref("bf_document_nextcloud_sync.menu_nc_document_config")
        self.assertIn(config_menu.id, Menu._visible_menu_ids())
