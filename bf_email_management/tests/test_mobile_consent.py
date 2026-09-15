"""La page d'accord à l'appariement (audit du 2026-09-08, S-M1).

``/auth/start`` émettait le code d'appariement sur un simple GET authentifié et
repartait aussitôt vers le schéma de l'app. Une app tierce du téléphone qui
déclare ce schéma pouvait ouvrir l'URL dans le navigateur où la personne est
connectée, avec SON défi PKCE, et apparier un appareil sur sa boîte sans
qu'elle voie rien. PKCE ne protège pas contre l'initiateur.

Chaque essai porte la valeur qui ferait passer l'ancienne route : un GET qui
rend un code, un POST sans jeton CSRF, un refus qui émettrait quand même, des
champs cachés qu'on ne revérifierait pas.
"""
import base64
import hashlib
import json
import re
import secrets

from odoo.tests import HttpCase, tagged

BASE = "/bf_email_management/mobile/v1"
SCHEMA = "odooinbox://auth"


def _pkce():
    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("utf-8")).digest()).decode().rstrip("=")
    return verifier, challenge


@tagged("post_install", "-at_install")
class TestMobileConsent(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # ⚠️ Les deux langues actives : une base montée en fr_CA seul n'a pas
        # en_US, et `get_lang` y ramène tout le monde au français.
        for code in ("en_US", "fr_CA"):
            cls.env["res.lang"]._activate_lang(code)
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        group_user = cls.env.ref("base.group_user")
        cls.owner = Users.create({
            "name": "Porteuse Accord", "login": "mobile.accord@test.invalid",
            "password": "mobile.accord@test.invalid",
            "email": "accord@test.invalid", "lang": "en_US",
            "groups_id": [(6, 0, [group_user.id])],
        })
        cls.francophone = Users.create({
            "name": "Porteur Francophone", "login": "mobile.accord.fr@test.invalid",
            "password": "mobile.accord.fr@test.invalid",
            "email": "accord.fr@test.invalid", "lang": "fr_CA",
            "groups_id": [(6, 0, [group_user.id])],
        })
        cls.sans_boite = Users.create({
            "name": "Sans Boîte", "login": "mobile.sansboite@test.invalid",
            "password": "mobile.sansboite@test.invalid",
            "groups_id": [(6, 0, [group_user.id])],
        })
        for user in (cls.owner, cls.francophone):
            cls.env["bf.email.account"].create({
                "name": "Boîte %s" % user.name, "user_id": user.id,
                "host": "imap.test.invalid", "port": 993,
                "login": user.email, "password": "x", "state": "connected",
            })
        cls.env["ir.config_parameter"].sudo().set_param(
            "bf_email_management.mobile_redirect_schemes", "odooinbox://")
        cls.Device = cls.env["bf.email.mobile.device"]

    def _pending(self):
        return self.Device.sudo().search_count([("pending_code", "!=", False)])

    def _start(self, challenge, login="mobile.accord@test.invalid", **extra):
        self.authenticate(login, login)
        params = {"redirect": SCHEMA, "state": "etat-7", "code_challenge": challenge,
                  "code_challenge_method": "S256", **extra}
        query = "&".join("%s=%s" % (k, v) for k, v in params.items())
        return self.url_open(BASE + "/auth/start?" + query,
                             timeout=30, allow_redirects=False)

    @staticmethod
    def _fields(page):
        return dict(re.findall(r'name="([a-z_]+)" value="([^"]*)"', page))

    def _answer(self, fields, decision):
        return self.url_open(BASE + "/auth/consent",
                             data=dict(fields, decision=decision),
                             timeout=30, allow_redirects=False)

    # ---------------------------------------------------------------- GET
    def test_the_get_renders_consent_and_issues_nothing(self):
        _verifier, challenge = _pkce()
        before = self._pending()
        response = self._start(challenge, device_name="Pixel%20de%20banc")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.headers.get("Location"))
        self.assertEqual(self._pending(), before,
                         "A pairing code was issued without consent.")
        page = response.text
        self.assertNotIn("code=", page)
        self.assertIn("Symbifox Mobile", page)
        self.assertIn("Porteuse Accord", page)
        self.assertIn("mobile.accord@test.invalid", page)
        self.assertIn("Pixel de banc", page)
        self.assertIn("Your email", page)
        fields = self._fields(page)
        self.assertTrue(fields.get("csrf_token"))
        self.assertEqual(fields["code_challenge"], challenge)
        self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")
        self.assertIn("frame-ancestors 'none'",
                      response.headers.get("Content-Security-Policy", ""))

    def test_the_device_name_is_escaped(self):
        _verifier, challenge = _pkce()
        response = self._start(challenge, device_name="%3Cimg%20src=x%20onerror=alert(1)%3E")
        self.assertNotIn("<img", response.text)
        self.assertIn("&lt;img", response.text)

    def test_the_page_speaks_the_users_language(self):
        _verifier, challenge = _pkce()
        page = self._start(challenge, login="mobile.accord.fr@test.invalid").text
        self.assertIn(">Autoriser<", page)
        self.assertIn(">Refuser<", page)
        self.assertIn("Votre courriel", page)
        self.assertIn('lang="fr"', page)

    def test_validation_errors_still_bounce_through_the_deep_link(self):
        _verifier, challenge = _pkce()
        response = self._start(challenge, login="mobile.sansboite@test.invalid")
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=no_mailbox", response.headers["Location"])

    # --------------------------------------------------------------- POST
    def test_a_post_without_csrf_is_refused(self):
        _verifier, challenge = _pkce()
        fields = self._fields(self._start(challenge).text)
        fields.pop("csrf_token")
        before = self._pending()
        response = self._answer(fields, "allow")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("code=", response.headers.get("Location", ""))
        self.assertEqual(self._pending(), before)

    def test_deny_bounces_access_denied(self):
        _verifier, challenge = _pkce()
        fields = self._fields(self._start(challenge).text)
        before = self._pending()
        response = self._answer(fields, "deny")
        location = response.headers["Location"]
        self.assertTrue(location.startswith(SCHEMA))
        self.assertIn("error=access_denied", location)
        self.assertIn("state=etat-7", location)
        self.assertNotIn("code=", location)
        self.assertEqual(self._pending(), before)

    def test_allow_issues_a_code_the_verifier_exchanges(self):
        verifier, challenge = _pkce()
        fields = self._fields(self._start(challenge, device_name="Pixel").text)
        response = self._answer(fields, "allow")
        self.assertIn(response.status_code, (302, 303))
        location = response.headers["Location"]
        self.assertTrue(location.startswith(SCHEMA))
        self.assertIn("state=etat-7", location)
        code = re.search(r"[?&]code=([^&]+)", location).group(1)
        exchange = self.url_open(
            BASE + "/auth/exchange",
            data=json.dumps({"code": code, "code_verifier": verifier}).encode(),
            headers={"Content-Type": "application/json"}, timeout=30)
        self.assertEqual(exchange.status_code, 200, exchange.text)
        body = exchange.json()
        self.assertTrue(body["token"])
        self.assertEqual(body["user_id"], self.owner.id)
        self.assertEqual(self.Device._resolve(body["token"]).name, "Pixel")

    def test_the_post_revalidates_the_redirect(self):
        _verifier, challenge = _pkce()
        fields = self._fields(self._start(challenge).text)
        fields["redirect"] = "https://malveillant.test/vol"
        before = self._pending()
        response = self._answer(fields, "allow")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("code=", response.headers.get("Location", ""))
        self.assertEqual(self._pending(), before)

    def test_the_post_revalidates_pkce(self):
        _verifier, challenge = _pkce()
        fields = self._fields(self._start(challenge).text)
        fields["code_challenge"] = ""
        before = self._pending()
        response = self._answer(fields, "allow")
        self.assertIn("error=pkce_required", response.headers["Location"])
        self.assertEqual(self._pending(), before)

    def test_the_post_revalidates_the_mailbox(self):
        """The user comes from the session: someone without a mailbox who
        replays another person's form gets nothing."""
        _verifier, challenge = _pkce()
        fields = self._fields(self._start(challenge).text)
        self.authenticate("mobile.sansboite@test.invalid", "mobile.sansboite@test.invalid")
        token = re.search(r'csrf_token: "([^"]+)"', self.url_open("/web", timeout=30).text)
        self.assertTrue(token, "The bench found no CSRF token on /web.")
        fields["csrf_token"] = token.group(1)
        before = self._pending()
        response = self._answer(fields, "allow")
        self.assertIn("error=no_mailbox", response.headers.get("Location", ""))
        self.assertNotIn("code=", response.headers.get("Location", ""))
        self.assertEqual(self._pending(), before)
