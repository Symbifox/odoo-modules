"""Lien de connexion Odoo à usage unique de la clé USB live.

Ce qui est gardé ici :

- le contrat : POST /api/v1/policy/live/login-link rend une URL valable 5 min ;
  l'ouvrir ouvre la session Odoo de la personne et mène à /odoo ;
- le jeton : jamais en clair dans la base, une seule ouverture, refusé expiré,
  refusé pour un autre usager, refusé si la personne est devenue portail ou
  archivée entre la demande et l'ouverture ;
- QUI obtient un lien : les mêmes contrôles que la clé de profil (client live,
  `sub`, portail refusé), un jeton dont `amr` porte `mfa` (vérifié par Odoo, le
  client de la clé live étant public), plus un plafond de liens par heure ;
- un lien ne bascule jamais un navigateur déjà connecté sous un AUTRE compte ;
- la 2FA d'Odoo (TOTP, ou code par courriel imposé aux employés) n'est pas
  redemandée : le QR l'a déjà exigée chez Authentik ;
- le justificatif maison n'est accepté QUE par la route du lien : ni par
  /web/session/authenticate, ni par /web/login (qui laisse le client choisir
  `type`), ni par res.users.authenticate appelé d'ailleurs ;
- le lien ne s'ouvre que sur une navigation lancée par le navigateur lui-même
  (`Sec-Fetch-Site: none`), jamais au bout d'un lien suivi dans une page, ni
  derrière /web/session/logout?redirect=… ; une session à moitié ouverte
  (2FA en attente) compte comme ouverte.
"""

import hashlib
import json
import re
from datetime import timedelta
from urllib.parse import urlencode
from unittest.mock import patch

from odoo import fields, http
from odoo.exceptions import AccessDenied, AccessError
from odoo.service.model import get_public_method
from odoo.tests import HttpCase, tagged

from odoo.addons.bf_policy_live.models import live_login

from .test_live_profile import _HOST, _INSTALL, _INTROSPECT, _PASSWORD, _Base, _jwt, _live_token

_URL = "/api/v1/policy/live/login-link"


@tagged("post_install", "-at_install")
class TestLiveLoginLink(_Base, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_data()
        cls.env.flush_all()

    # --- outils --------------------------------------------------------------
    def _ask(self, claims=None, bearer=None, body=None):
        headers = {"Host": _HOST, "Content-Type": "application/json"}
        bearer = _live_token() if bearer is None else bearer
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        claims = {"email": self.jane.login} if claims is None else claims
        self.env.flush_all()
        with patch(_INTROSPECT, return_value=claims):
            resp = self.url_open(_URL, data=json.dumps({} if body is None else body),
                                 headers=headers)
        self.env.invalidate_all()
        return resp

    def _link(self, **kw):
        resp = self._ask(**kw)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["expires_in"], 300)
        m = re.fullmatch(r"https?://[^/]+(/odoo\?bfos_login=([A-Za-z0-9_-]+))", body["url"])
        self.assertTrue(m, body["url"])
        return m.group(1), m.group(2)

    def _session_uid(self):
        sid = self.opener.cookies.get("session_id")
        if not sid:
            return None
        return http.root.session_store.get(sid).get("uid")

    def _open(self, path, site="none"):
        """Ouvre le lien dans un navigateur neuf (sans cookie). `site` : l'en-tête
        Sec-Fetch-Site du navigateur ("none" = adresse ouverte par le navigateur
        lui-même, comme la clé live qui lance Brave dessus)."""
        self.opener.cookies.clear()
        self.env.flush_all()
        resp = self.url_open(path, allow_redirects=False,
                             headers={"Sec-Fetch-Site": site} if site else {})
        self.env.invalidate_all()
        return resp

    def _links(self):
        """Les liens des comptes d'essai seulement : une base copiée d'une
        instance réelle porte déjà les siens."""
        return self.env["bf.policy.live.login"].sudo().search(
            [("user_id.login", "=like", "%@live.example.test")])

    def _row(self, raw):
        return self.env["bf.policy.live.login"].sudo().search(
            [("token_hash", "=", hashlib.sha256(raw.encode()).hexdigest())])

    def _authenticate_direct(self, login, raw, marker):
        """res.users.authenticate appelé hors de la route, marqueur au choix."""
        token = live_login._LINK_ROUTE.set(marker)
        try:
            return self.env["res.users"].authenticate(
                self.env.cr.dbname,
                {"type": live_login.LINK_TYPE, "login": login,
                 live_login.TOKEN_KEY: raw},
                {"interactive": True})
        finally:
            live_login._LINK_ROUTE.reset(token)

    # --- contrat -------------------------------------------------------------
    def test_link_opens_the_session_once(self):
        path, raw = self._link()
        self.assertEqual(self._ask().status_code, 200, "un 2e lien peut être tiré")
        row = self._row(raw)
        self.assertEqual(len(row), 1)
        self.assertEqual(row.user_id, self.jane)
        self.assertFalse(row.used)
        # Jamais le jeton en clair : seule son empreinte.
        self.assertNotEqual(row.token_hash, raw)
        self.env.cr.execute("SELECT count(*) FROM bf_policy_live_login "
                            "WHERE token_hash = %s OR created_ip = %s", (raw, raw))
        self.assertEqual(self.env.cr.fetchone()[0], 0)
        self.assertLessEqual(row.expires_at,
                             fields.Datetime.now() + timedelta(seconds=301))

        resp = self._open(path)
        self.assertEqual(resp.status_code, 303, resp.text)
        self.assertTrue(resp.headers["Location"].endswith("/odoo"), resp.headers)
        self.assertEqual(self._session_uid(), self.jane.id)
        row.invalidate_recordset()
        self.assertTrue(row.used)
        self.assertTrue(row.used_at)

        # Deuxième ouverture, autre navigateur : refusée, aucune session.
        again = self._open(path)
        self.assertEqual(again.status_code, 303)
        self.assertIn("/web/login", again.headers["Location"])
        self.assertFalse(self._session_uid())

    def test_odoo_without_the_parameter_is_unchanged(self):
        resp = self._open("/odoo")
        self.assertEqual(resp.status_code, 303)
        self.assertIn("/web/login", resp.headers["Location"])
        self.assertIn("redirect=", resp.headers["Location"])
        self.assertFalse(self._session_uid())
        # Session ouverte normalement : /odoo sert le client web, pas une redirection.
        self.authenticate(self.jane.login, _PASSWORD)
        # Rendu complet du client web : sur une base qui a beaucoup de modules, la
        # compilation des styles dans la transaction de l'essai dépasse les 12 s par défaut.
        resp = self.url_open("/odoo", allow_redirects=False, timeout=120)
        self.assertEqual(resp.status_code, 200)

    def test_stale_link_with_an_open_session_stays_in(self):
        self.authenticate(self.jane.login, _PASSWORD)
        self.env.flush_all()
        resp = self.url_open("/odoo?bfos_login=inconnu", allow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["Location"].endswith("/odoo"), resp.headers["Location"])
        self.assertEqual(self._session_uid(), self.jane.id)

    def test_link_on_a_deeper_path_keeps_it(self):
        _path, raw = self._link()
        resp = self._open(f"/odoo/action-xyz?debug=1&bfos_login={raw}")
        self.assertEqual(resp.status_code, 303, resp.text)
        self.assertTrue(resp.headers["Location"].endswith("/odoo/action-xyz?debug=1"),
                        resp.headers["Location"])
        self.assertEqual(self._session_uid(), self.jane.id)

    # --- session Authentik du navigateur (bfos_auth) ---------------------
    _NAV = "https://auth.exemple.test/if/flow/bfos-live-navigateur/"
    _CODE = "A" * 21 + "b_-" + "c" * 19   # 43 caractères base64url

    def _nav(self, url):
        self.env["ir.config_parameter"].sudo().set_param("bf_policy_live.authentik_nav_url", url)
        self.env.flush_all()

    def test_consumed_link_hands_the_code_to_authentik(self):
        self._nav(self._NAV)
        _path, raw = self._link()
        resp = self._open(f"/odoo?bfos_login={raw}&bfos_auth={self._CODE}")
        self.assertEqual(resp.status_code, 303, resp.text)
        self.assertEqual(resp.headers["Location"], self._NAV + "?c=" + self._CODE)
        self.assertEqual(self._session_uid(), self.jane.id, "la session Odoo est ouverte d'abord")

    def test_code_ignored_without_config_or_malformed(self):
        for nav, code in (("", self._CODE), (self._NAV, "court"), (self._NAV, self._CODE + "x"),
                          ("http://auth.exemple.test/flux/", self._CODE),
                          ("https://auth.exemple.test/flux/?next=//ailleurs", self._CODE)):
            self._nav(nav)
            _path, raw = self._link()
            resp = self._open(f"/odoo?bfos_login={raw}&bfos_auth={code}")
            self.assertEqual(resp.status_code, 303, (nav, code))
            self.assertTrue(resp.headers["Location"].endswith("/odoo"), (nav, code, resp.headers))

    def test_code_dropped_from_a_deeper_path_without_relay(self):
        self._nav("")
        _path, raw = self._link()
        resp = self._open(f"/odoo/action-xyz?debug=1&bfos_auth={self._CODE}&bfos_login={raw}")
        self.assertEqual(resp.status_code, 303, resp.text)
        self.assertTrue(resp.headers["Location"].endswith("/odoo/action-xyz?debug=1"),
                        resp.headers["Location"])

    def test_refused_link_never_relays_the_code(self):
        self._nav(self._NAV)
        resp = self._open(f"/odoo?bfos_login=inconnu&bfos_auth={self._CODE}")
        self.assertIn("/web/login", resp.headers["Location"])
        # Session déjà ouverte, lien périmé : on reste dans Odoo, sans relais.
        self.authenticate(self.jane.login, _PASSWORD)
        self.env.flush_all()
        resp = self.url_open(f"/odoo?bfos_login=inconnu&bfos_auth={self._CODE}", allow_redirects=False)
        self.assertTrue(resp.headers["Location"].endswith("/odoo"), resp.headers["Location"])

    def test_unknown_and_malformed_tokens(self):
        for path in ("/odoo?bfos_login=inconnu", "/odoo?bfos_login=",
                     "/odoo?bfos_login=" + "a" * 200):
            resp = self._open(path)
            self.assertIn("/web/login", resp.headers.get("Location", ""), path)
            self.assertFalse(self._session_uid())

    def test_expired_link_is_refused(self):
        path, raw = self._link()
        self._row(raw).write({"expires_at": fields.Datetime.now() - timedelta(seconds=1)})
        resp = self._open(path)
        self.assertIn("/web/login", resp.headers["Location"])
        self.assertFalse(self._session_uid())
        self.assertFalse(self._row(raw).used)

    def test_user_turned_portal_or_archived_is_refused(self):
        for i, change in enumerate((
                {"groups_id": [(6, 0, [self.env.ref("base.group_portal").id])]},
                {"active": False})):
            victim = self.env["res.users"].with_context(no_reset_password=True).create({
                "name": "Temp", "login": f"tmp{i}@live.example.test",
                "password": _PASSWORD, "company_id": self.company.id,
                "company_ids": [(6, 0, [self.company.id])],
                "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
            path, raw = self._link(claims={"email": victim.login},
                                   bearer=_live_token(f"sub-tmp{i}"))
            victim.write(change)
            resp = self._open(path)
            self.assertIn("/web/login", resp.headers["Location"], change)
            self.assertFalse(self._session_uid(), change)

    # --- qui obtient un lien -------------------------------------------------
    def test_token_of_another_client_gets_no_link(self):
        for bearer in (_jwt({"aud": _INSTALL, "azp": _INSTALL, "sub": "sub-jane"}),
                       _jwt({"sub": "sub-jane"}), "jeton-opaque"):
            resp = self._ask(bearer=bearer)
            self.assertEqual(resp.status_code, 401, bearer)
        resp = self._ask(bearer=_jwt({"aud": "blue-fox-os-live"}))
        self.assertEqual(resp.status_code, 401, "sans sub")
        self.assertFalse(self._links())

    def test_portal_gets_no_link(self):
        resp = self._ask(claims={"email": self.portal.login},
                         bearer=_live_token("sub-paul"))
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(self._links())

    def test_token_without_second_factor_gets_no_link(self):
        for amr in (["pwd"], [], None, "pwd", {"mfa": True}):
            extra = {} if amr is None else {"amr": amr}
            bearer = _jwt(dict({"aud": "blue-fox-os-live", "azp": "blue-fox-os-live",
                                "sub": "sub-jane"}, **extra))
            resp = self._ask(bearer=bearer)
            self.assertEqual(resp.status_code, 403, amr)
            self.assertEqual(resp.json(), {"error": "second factor required"}, amr)
        self.assertFalse(self._links())
        # Une chaîne seule vaut une liste d'un élément.
        self.assertEqual(self._ask(bearer=_live_token(amr="mfa")).status_code, 200)

    def test_half_open_session_counts_as_open(self):
        path, raw = self._link()
        self.opener.cookies.clear()
        self.authenticate(self.bob.login, _PASSWORD)
        sid = self.opener.cookies.get("session_id")
        sess = http.root.session_store.get(sid)
        sess["pre_uid"], sess["uid"] = self.bob.id, None
        http.root.session_store.save(sess)
        self.env.flush_all()
        self.url_open(path, allow_redirects=False, headers={"Sec-Fetch-Site": "none"})
        self.env.invalidate_all()
        self.assertFalse(self._row(raw).used, "2FA de Bob en attente : le lien de Jane ne sert pas")
        self.assertNotEqual(self._session_uid(), self.jane.id)

    def test_link_never_switches_another_users_session(self):
        # Bob a sa session ; on lui fait ouvrir le lien de Jane.
        path, raw = self._link()
        self.opener.cookies.clear()
        self.authenticate(self.bob.login, _PASSWORD)
        self.env.flush_all()
        resp = self.url_open(path, allow_redirects=False, headers={"Sec-Fetch-Site": "none"})
        self.env.invalidate_all()
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["Location"].endswith("/odoo"), resp.headers)
        self.assertNotIn("bfos_login", resp.headers["Location"])
        self.assertEqual(self._session_uid(), self.bob.id, "Bob reste Bob")
        self.assertFalse(self._row(raw).used, "le lien de Jane n'est pas consommé")
        # Rouvert dans un navigateur neuf, il sert encore à Jane.
        resp = self._open(path)
        self.assertEqual(self._session_uid(), self.jane.id)

    def test_link_followed_from_a_page_is_refused(self):
        path, raw = self._link()
        for site in (None, "cross-site", "same-origin", "same-site", "NONE"):
            resp = self._open(path, site=site)
            self.assertIn("/web/login", resp.headers["Location"], site)
            self.assertFalse(self._session_uid(), site)
            self.assertFalse(self._row(raw).used, f"{site} : pas consommé")
        # Ouvert par le navigateur lui-même, il sert encore.
        resp = self._open(path)
        self.assertEqual(self._session_uid(), self.jane.id)

    def test_logout_wrapped_link_does_not_switch_the_session(self):
        # Jane envoie à Bob /web/session/logout?redirect=<son lien> : la
        # déconnexion d'Odoo, en GET, vide la session de Bob puis le renvoie sur
        # le lien. Le navigateur suit la chaîne née d'un clic dans une page :
        # Sec-Fetch-Site n'y vaut jamais « none ».
        path, raw = self._link()
        self.opener.cookies.clear()
        self.authenticate(self.bob.login, _PASSWORD)
        self.env.flush_all()
        resp = self.url_open("/web/session/logout?" + urlencode({"redirect": path}),
                             headers={"Sec-Fetch-Site": "same-origin"})
        self.env.invalidate_all()
        uids = {http.root.session_store.get(c.value).get("uid")
                for c in self.opener.cookies if c.name == "session_id"}
        self.assertNotIn(self.jane.id, uids, resp.url)
        self.assertFalse(self._row(raw).used)

    def test_malformed_body(self):
        self.assertEqual(self._ask(body=[]).status_code, 400)

    def test_rate_limit(self):
        for _i in range(live_login.MAX_PER_HOUR):
            self.assertEqual(self._ask().status_code, 200)
        resp = self._ask()
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(len(self._links()), live_login.MAX_PER_HOUR)

    # --- 2FA d'Odoo ----------------------------------------------------------
    def test_totp_is_not_asked_again(self):
        self.env.cr.execute("UPDATE res_users SET totp_secret = %s WHERE id = %s",
                            ("JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP", self.jane.id))
        self.jane.invalidate_recordset()
        self.assertEqual(self.jane.with_user(self.jane)._mfa_type(), "totp")
        path, _raw = self._link()
        resp = self._open(path)
        self.assertTrue(resp.headers["Location"].endswith("/odoo"), resp.headers)
        self.assertEqual(self._session_uid(), self.jane.id)

    def test_mail_code_enforced_for_employees_is_not_asked(self):
        if "auth_totp_mail_enforce" not in self.env.registry._init_modules:
            self.skipTest("auth_totp_mail_enforce absent")
        self.env["ir.config_parameter"].sudo().set_param(
            "auth_totp.policy", "employee_required")
        self.assertEqual(self.jane.with_user(self.jane)._mfa_type(), "totp_mail")
        path, _raw = self._link()
        resp = self._open(path)
        self.assertTrue(resp.headers["Location"].endswith("/odoo"), resp.headers)
        self.assertEqual(self._session_uid(), self.jane.id)

    # --- le justificatif maison hors de la route -----------------------------
    def test_custom_credential_outside_the_route_is_refused(self):
        path, raw = self._link()
        # Appel direct, sans le marqueur de la route : refusé.
        with self.assertRaises(AccessDenied):
            self._authenticate_direct(self.jane.login, raw, marker=False)
        self.assertFalse(self._row(raw).used, "un refus ne consomme pas le lien")

        # /web/session/authenticate : le jeton comme mot de passe.
        self.opener.cookies.clear()
        resp = self.url_open("/web/session/authenticate", data=json.dumps({
            "jsonrpc": "2.0", "method": "call", "params": {
                "db": self.env.cr.dbname, "login": self.jane.login, "password": raw}}),
            headers={"Content-Type": "application/json"})
        self.assertIn("error", resp.json())
        self.assertFalse(self._session_uid())

        # /web/login : le client y choisit `type` ; le jeton n'y passe pas.
        self.opener.cookies.clear()
        page = self.url_open("/web/login").text
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        resp = self.url_open("/web/login", data={
            "csrf_token": csrf, "login": self.jane.login, "password": raw,
            "type": live_login.LINK_TYPE, live_login.TOKEN_KEY: raw},
            allow_redirects=False)
        self.assertNotEqual(resp.status_code, 303)
        self.assertFalse(self._session_uid())

        self.assertFalse(self._row(raw).used)
        # Le lien, lui, marche toujours.
        self.assertTrue(self._open(path).headers["Location"].endswith("/odoo"))

    def test_token_of_someone_else_is_refused(self):
        _path, raw = self._link()
        with self.assertRaises(AccessDenied):
            self._authenticate_direct(self.bob.login, raw, marker=True)
        self.assertFalse(self._row(raw).used)

    def test_consumption_rechecks_expiry_and_use(self):
        # La route regarde avant de consommer ; la consommation, elle, doit
        # refaire les deux contrôles (course, ou appel sans la route).
        _p, expired = self._link()
        self._row(expired).write({"expires_at": fields.Datetime.now() - timedelta(seconds=1)})
        with self.assertRaises(AccessDenied):
            self._authenticate_direct(self.jane.login, expired, marker=True)
        _p, raw = self._link()
        self._authenticate_direct(self.jane.login, raw, marker=True)
        with self.assertRaises(AccessDenied):
            self._authenticate_direct(self.jane.login, raw, marker=True)

    def test_internal_check_inside_the_credential(self):
        # Même marqueur posé : un usager non interne ne passe pas, garde
        # redoublée dans _check_credentials (la route n'est pas seule).
        _path, raw = self._link()
        self.jane.write({"groups_id": [(6, 0, [self.env.ref("base.group_portal").id])]})
        with self.assertRaises(AccessDenied):
            self._authenticate_direct(self.jane.login, raw, marker=True)

    def test_marker_and_valid_token_authenticate(self):
        _path, raw = self._link()
        info = self._authenticate_direct(self.jane.login, raw, marker=True)
        self.assertEqual(info, {"uid": self.jane.id, "auth_method": live_login.LINK_TYPE,
                                "mfa": "skip"})
        self.assertTrue(self._row(raw).used)

    # --- accès ---------------------------------------------------------------
    def test_nobody_writes_or_calls_the_service(self):
        Login = self.env["bf.policy.live.login"]
        for user in (self.jane, self.portal, self.admin):
            with self.assertRaises(AccessError):
                Login.with_user(user).create({
                    "token_hash": "x", "user_id": user.id,
                    "expires_at": fields.Datetime.now()})
            for name in ("_issue", "_consume", "_peek", "_gc_live_logins"):
                with self.assertRaises(AccessError):
                    get_public_method(Login.with_user(user), name)
        _path, raw = self._link()
        row = self._row(raw)
        with self.assertRaises(AccessError):
            row.with_user(self.admin).write({"used": False})
        with self.assertRaises(AccessError):
            row.with_user(self.jane).read(["user_id"])

    def test_autovacuum_purges_old_links(self):
        _p1, old = self._link()
        _p2, fresh = self._link()
        self._row(old).write({"expires_at": fields.Datetime.now() - timedelta(days=2)})
        self.env["bf.policy.live.login"]._gc_live_logins()
        self.assertFalse(self._row(old))
        self.assertTrue(self._row(fresh))
