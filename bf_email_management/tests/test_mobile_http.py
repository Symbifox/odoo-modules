"""Les routes elles-mêmes : authentification, codes de retour, redirections.

Les autres fichiers éprouvent la couche modèle. Ici on passe par HTTP, parce
que c'est là que vivent le décorateur d'authentification, la traduction des
erreurs en codes, et la garde anti-redirection ouverte — dont aucune n'est
visible depuis l'ORM.
"""
import json

from odoo.tests import HttpCase, tagged

from .common import build_rfc822

BASE = "/bf_email_management/mobile/v1"


@tagged("post_install", "-at_install")
class TestMobileHttp(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Porteur HTTP",
            "login": "mobile.http@test.invalid",
            "email": "http@test.invalid",
            "tz": "America/Montreal",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.account = cls.env["bf.email.account"].create({
            "name": "Boîte HTTP", "user_id": cls.owner.id,
            "host": "imap.test.invalid", "port": 993,
            "login": "http@test.invalid", "password": "x", "state": "connected",
        })
        cls.email = cls.env["bf.email"].with_user(cls.owner).create({
            "subject": "Sujet HTTP", "email_from": "tiers@test.invalid",
            "email_to": "http@test.invalid", "direction": "in", "status": "new",
            "source": "imap", "account_id": cls.account.id,
            "user_id": cls.owner.id, "imap_in_inbox": True,
            "message_id_header": "<http-1@test.invalid>",
            "date": "2026-08-14 12:00:00",
            "raw_rfc822": build_rfc822("Sujet HTTP", "tiers@test.invalid",
                                       "http@test.invalid", "Corps."),
        })
        cls.device = cls.env["bf.email.mobile.device"]._issue(
            cls.owner.id, name="Appareil HTTP")
        cls.env.cr.flush()

    def _auth(self):
        return {"Authorization": "Bearer %s" % self.device.device_token}

    def _get(self, path, headers=None):
        return self.url_open(BASE + path, headers=headers or {}, timeout=30)

    def _post(self, path, payload, headers=None):
        merged = {"Content-Type": "application/json", **(headers or {})}
        return self.url_open(BASE + path, data=json.dumps(payload).encode(),
                             headers=merged, timeout=30)

    # ------------------------------------------------------------ découverte
    def test_ping_is_public_and_names_the_module(self):
        response = self._get("/ping")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["module"], "bf_email_management")

    # --------------------------------------------------- authentification
    def test_every_data_route_refuses_an_anonymous_caller(self):
        for path in ("/config", "/threads?filter=inbox",
                     "/conversation?thread_key=id:1", "/message?id=1",
                     "/attachment?email_id=1&idx=0", "/records?model=res.partner&q=ab"):
            with self.subTest(path=path):
                self.assertEqual(self._get(path).status_code, 401)
        for path in ("/mark_read", "/handle", "/snooze", "/reply", "/compose",
                     "/route", "/spawn", "/register_push", "/attachment/upload"):
            with self.subTest(path=path):
                self.assertEqual(self._post(path, {}).status_code, 401)

    def test_a_bogus_token_is_refused(self):
        response = self._get("/config", {"Authorization": "Bearer pas-un-jeton"})
        self.assertEqual(response.status_code, 401)

    def test_a_malformed_authorization_header_is_refused(self):
        for header in ("", "Basic abc", "Bearer", "bearer x", "Token abc"):
            with self.subTest(header=header):
                self.assertEqual(
                    self._get("/config", {"Authorization": header}).status_code, 401)

    def test_an_authenticated_call_serves_that_users_mailbox(self):
        response = self._get("/config", self._auth())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["user_name"], "Porteur HTTP")
        self.assertTrue(body["accounts"])

    # ------------------------------------------------------------- erreurs
    def test_a_bad_parameter_is_a_400_not_a_500(self):
        """Un 500 dirait à l'app de réessayer indéfiniment une requête fautive."""
        self.assertEqual(
            self._get("/threads?filter=inexistant", self._auth()).status_code, 400)
        self.assertEqual(
            self._post("/mark_read", {"email_ids": "abc"}, self._auth()).status_code,
            400)
        self.assertEqual(
            self._post("/reply", {}, self._auth()).status_code, 400)

    def test_a_missing_record_is_a_404(self):
        self.assertEqual(
            self._post("/reply", {"email_id": 99999999, "mode": "reply",
                                  "body": "x"}, self._auth()).status_code, 404)

    def test_form_encoded_body_still_understood(self):
        """L'app envoie du JSON ; un mauvais Content-Type ne doit pas devenir
        un silence trompeur ("aucun courriel visé") mais fonctionner."""
        response = self.url_open(
            BASE + "/mark_read",
            data={"email_ids": str(self.email.id)},
            headers=self._auth(), timeout=30,
        )
        self.assertEqual(response.status_code, 200)

    # ------------------------------------------- garde anti-redirection ouverte
    def test_auth_start_refuses_a_foreign_redirect(self):
        """Sans cette garde, la route remet un code d'échange vivant à l'URL
        de son choix."""
        self.authenticate("mobile.http@test.invalid", "mobile.http@test.invalid")
        response = self.url_open(
            BASE + "/auth/start?redirect=https://malveillant.test/vol&state=x",
            timeout=30, allow_redirects=False)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("code=", response.text)

    def test_auth_start_bounces_to_an_allowed_scheme(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_email_management.mobile_redirect_schemes", "odooinbox://")
        self.authenticate("mobile.http@test.invalid", "mobile.http@test.invalid")
        response = self.url_open(
            BASE + "/auth/start?redirect=odooinbox://auth&state=abc",
            timeout=30, allow_redirects=False)
        self.assertEqual(response.status_code, 302)
        location = response.headers.get("Location", "")
        self.assertTrue(location.startswith("odooinbox://auth"))
        self.assertIn("code=", location)
        self.assertIn("state=abc", location)

    # -------------------------------------------------------- push endpoint
    def test_private_push_endpoints_are_refused(self):
        """Le serveur POSTe vers cette URL depuis un cron : une adresse interne
        en ferait un relais SSRF aveugle."""
        for endpoint in ("http://127.0.0.1:8080/up",
                         "http://169.254.169.254/latest/meta-data",
                         "http://192.168.1.1/up",
                         "ftp://exemple.test/up",
                         "pas-une-url"):
            with self.subTest(endpoint=endpoint):
                response = self._post("/register_push", {"endpoint": endpoint},
                                      self._auth())
                self.assertEqual(response.status_code, 400)

    # ------------------------------------------------------------- upload
    def test_upload_requires_a_file(self):
        self.assertEqual(
            self._post("/attachment/upload", {}, self._auth()).status_code, 400)
