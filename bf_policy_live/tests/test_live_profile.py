"""Clé de profil persistant de la clé USB live.

Ce qui est gardé ici :

- le contrat HTTP : création, relecture identique, 404 sans « create », 503
  sans séquestre (et rien de créé), 403 sur une fiche révoquée (et rien de
  recréé), suppression qui lève la révocation ;
- QUI obtient une clé : seul un jeton émis au client live (aud ou azp), que la
  vérification d'audience de l'organisation soit coupée (cas courant) ou
  allumée ; ni un jeton d'installation, ni d'une autre application, ni opaque ;
  pas un compte portail ; pas un autre compte Authentik que le créateur ;
- QUI lit la fiche : ni un interne ordinaire, ni un portail ; un administrateur
  lit la fiche, jamais la clé ; aucune méthode de service n'est appelable par
  RPC, et la révocation exige le droit dédié ;
- la clé n'est dans aucun journal, même au niveau DEBUG (requêtes SQL comprises).

Le point userinfo d'Authentik est remplacé par un double, comme dans les essais
de bf_policy : le vrai chemin du contrôleur est exercé, jeton compris.
"""

import base64
import json
import os
from unittest.mock import patch

from odoo.exceptions import AccessError, ValidationError
from odoo.service.model import get_public_method
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.bf_policy.models import escrow

_INTROSPECT = "odoo.addons.bf_policy.controllers.main._introspect"
_URL = "/api/v1/policy/live/profile-key"
_HOST = "live.example.test"
_PASSWORD = "Essai-Live-2026!"
_LIVE = "blue-fox-os-live"
_INSTALL = "blue-fox-os"


def _a_key():
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def _jwt(claims):
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode()
    return "e30." + body.rstrip("=") + ".sig"


def _live_token(sub="sub-jane", **extra):
    # Comme Authentik après un QR avec 2FA : `amr` porte `mfa`.
    return _jwt(dict({"aud": _LIVE, "azp": _LIVE, "sub": sub, "amr": ["pwd", "mfa"]}, **extra))


def _users(env, company, *specs):
    out = []
    for name, login, groups in specs:
        out.append(env["res.users"].with_context(no_reset_password=True).create({
            "name": name, "login": login, "password": _PASSWORD,
            "company_id": company.id, "company_ids": [(6, 0, [company.id])],
            "groups_id": [(6, 0, [env.ref(g).id for g in groups])],
        }))
    return out


class _Base:
    """Données communes aux deux familles d'essais."""

    @classmethod
    def _setup_data(cls):
        cls.Org = cls.env["bf.policy.org"]
        cls.Org.search([]).write({"active": False})
        cls.company = cls.env["res.company"].create({"name": "Live Test Co."})
        cls.org = cls.Org.create({
            "company_id": cls.company.id, "domain": _HOST,
            "provision_mode": "any",
            # Cas courant : vérification d'audience de l'installation coupée.
            "token_audience_check": False,
            "oidc_client_id": _INSTALL,
            "authentik_userinfo_url": "https://idp.example.test/userinfo/"})
        cls.jane, cls.bob, cls.portal, cls.admin, cls.officer = _users(
            cls.env, cls.company,
            ("Jane Live", "jane@live.example.test", ["base.group_user"]),
            ("Bob Interne", "bob@live.example.test", ["base.group_user"]),
            ("Paul Portail", "paul@live.example.test", ["base.group_portal"]),
            ("Ada Admin", "ada@live.example.test", ["base.group_system"]),
            ("Olga Sequestre", "olga@live.example.test",
             ["base.group_system", "bf_policy.group_disk_escrow_read"]))
        cls.key = _a_key()

    def _with_key(self, key=None):
        return patch.dict(os.environ,
                          {escrow.ENV_VAR: key if key is not None else self.key})

    def _without_key(self):
        from odoo.tools import config as odoo_config
        return (patch.dict(os.environ, {escrow.ENV_VAR: ""}),
                patch.object(odoo_config, "get", return_value=""))

    def _profiles(self, user=None):
        self.env.invalidate_all()
        return self.env["bf.policy.live.profile"].sudo().search(
            [("user_id", "=", (user or self.jane).id)])


@tagged("post_install", "-at_install")
class TestLiveProfileKeyHttp(_Base, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_data()
        cls.env.flush_all()

    def _call(self, body=None, claims=None, bearer=None, key=True):
        headers = {"Host": _HOST, "Content-Type": "application/json"}
        bearer = _live_token() if bearer is None else bearer
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        claims = {"email": self.jane.login} if claims is None else claims
        self.env.flush_all()
        data = json.dumps({"create": True} if body is None else body)
        with patch(_INTROSPECT, return_value=claims):
            if key:
                with self._with_key():
                    resp = self.url_open(_URL, data=data, headers=headers)
            else:
                env_p, cfg_p = self._without_key()
                with env_p, cfg_p:
                    resp = self.url_open(_URL, data=data, headers=headers)
        self.env.invalidate_all()
        return resp

    # --- contrat -------------------------------------------------------------
    def test_token_without_second_factor_gets_no_key(self):
        bearer = _jwt({"aud": _LIVE, "azp": _LIVE, "sub": "sub-jane", "amr": ["pwd"]})
        resp = self._call(bearer=bearer)
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertEqual(resp.json(), {"error": "second factor required"})
        self.assertFalse(self._profiles(self.jane))

    def test_create_then_read_back_the_same_key(self):
        first = self._call({"create": True})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertIn("no-store", first.headers.get("Cache-Control", ""))
        body = first.json()
        self.assertTrue(body["created"])
        self.assertEqual(len(base64.b64decode(body["key"], validate=True)), 32)

        again = self._call({"create": False})
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json(), {"key": body["key"], "created": False})
        # create vrai sur une fiche existante : la même clé, jamais une autre.
        third = self._call({"create": True})
        self.assertEqual(third.json(), {"key": body["key"], "created": False})

        profile = self._profiles()
        self.assertEqual(len(profile), 1)
        self.assertEqual(profile.serve_count, 3)
        self.assertTrue(profile.last_served_on)
        stored = profile.key_enc
        self.assertTrue(stored)
        self.assertNotIn(body["key"], stored, "la clé ne doit pas être en clair")
        with self._with_key():
            self.assertEqual(escrow.decrypt(stored), body["key"])

    def test_two_people_get_two_keys(self):
        k1 = self._call({"create": True}).json()["key"]
        k2 = self._call({"create": True}, claims={"email": self.bob.login},
                        bearer=_live_token("sub-bob")).json()["key"]
        self.assertNotEqual(k1, k2)

    def test_no_profile_without_create(self):
        for body in ({"create": False}, {}, {"create": "true"}, {"create": 1}):
            resp = self._call(body)
            self.assertEqual(resp.status_code, 404, body)
            self.assertEqual(resp.json(), {"error": "no profile"})
        self.assertFalse(self._profiles())

    def test_malformed_body(self):
        for body in ([], "create", 3):
            self.assertEqual(self._call(body).status_code, 400, body)
        self.assertFalse(self._profiles())

    def test_escrow_unavailable_is_503_and_creates_nothing(self):
        resp = self._call({"create": True}, key=False)
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json(), {"error": "escrow unavailable"})
        self.assertFalse(self._profiles())

    def test_revoked_is_403_and_never_recreated(self):
        key = self._call({"create": True}).json()["key"]
        profile = self._profiles()
        profile.with_user(self.officer).action_revoke()
        self.env.flush_all()
        profile.invalidate_recordset()
        self.assertTrue(profile.revoked)
        self.assertFalse(profile.sudo().key_enc, "révoquer efface la clé")
        self.assertEqual(profile.revoked_by, self.officer)
        for body in ({"create": True}, {"create": False}):
            resp = self._call(body)
            self.assertEqual(resp.status_code, 403, body)
            self.assertEqual(resp.json(), {"error": "revoked"})
        self.assertEqual(len(self._profiles()), 1)
        # Lever la révocation = supprimer la fiche : profil neuf, clé neuve.
        self._profiles().with_user(self.officer).unlink()
        fresh = self._call({"create": True}).json()
        self.assertTrue(fresh["created"])
        self.assertNotEqual(fresh["key"], key)

    # --- qui obtient une clé -------------------------------------------------
    def test_token_of_another_client_is_refused(self):
        for bearer in (_jwt({"aud": _INSTALL, "azp": _INSTALL, "sub": "sub-jane"}),
                       _jwt({"aud": "nextcloud", "azp": "nextcloud", "sub": "sub-jane"}),
                       _jwt({"sub": "sub-jane"}),
                       "jeton-opaque"):
            resp = self._call({"create": True}, bearer=bearer)
            self.assertEqual(resp.status_code, 401, bearer)
            self.assertEqual(resp.json(),
                             {"error": "token not issued to the live key client"})
        self.assertFalse(self._profiles())

    def test_aud_list_or_azp_alone_is_enough(self):
        resp = self._call(bearer=_jwt({"aud": ["x", _LIVE], "sub": "sub-jane", "amr": ["mfa"]}))
        self.assertEqual(resp.status_code, 200)
        resp = self._call(bearer=_jwt({"aud": "x", "azp": _LIVE, "sub": "sub-jane", "amr": ["mfa"]}))
        self.assertEqual(resp.status_code, 200)

    def test_with_audience_check_on(self):
        # Vérification de l'organisation allumée : bf_policy exigerait le client
        # d'installation ; la clé live doit pourtant passer, et l'installation non.
        self.org.write({"token_audience_check": True})
        self.assertEqual(self._call().status_code, 200)
        resp = self._call(bearer=_jwt({"aud": _INSTALL, "azp": _INSTALL,
                                       "sub": "sub-jane"}))
        self.assertEqual(resp.status_code, 401)

    def test_empty_live_client_serves_nobody(self):
        self.org.write({"live_oidc_client_id": False})
        for bearer in (_live_token(), _jwt({"aud": "", "azp": "", "sub": "s"}),
                       _jwt({"sub": "s"})):
            self.assertEqual(self._call(bearer=bearer).status_code, 401, bearer)

    def test_token_without_subject_is_refused(self):
        resp = self._call(bearer=_jwt({"aud": _LIVE, "azp": _LIVE}))
        self.assertEqual(resp.status_code, 401)
        self.assertFalse(self._profiles())

    def test_no_bearer_and_odoo_session_are_refused(self):
        self.assertEqual(self._call(bearer="").status_code, 401)
        self.authenticate(self.jane.login, _PASSWORD)
        resp = self.url_open(_URL, data=json.dumps({"create": True}),
                             headers={"Host": _HOST,
                                      "Content-Type": "application/json"})
        self.assertEqual(resp.status_code, 401)
        self.assertFalse(self._profiles())

    def test_portal_user_is_refused(self):
        resp = self._call(claims={"email": self.portal.login},
                          bearer=_live_token("sub-paul"))
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(self._profiles(self.portal))

    def test_unauthorized_user_gets_nothing(self):
        # Sortir quelqu'un du mode de provisionnement coupe aussi sa clé live.
        self.assertEqual(self._call().status_code, 200)
        self.org.write({"provision_mode": "allowlist",
                        "provision_user_ids": [(6, 0, [self.bob.id])]})
        resp = self._call({"create": False})
        self.assertEqual(resp.status_code, 403)
        self.assertNotIn("key", resp.json())

    def test_another_authentik_account_cannot_take_over(self):
        # Un autre compte Authentik (autre `sub`) qui se ferait rattacher à
        # Jane, p. ex. en prenant son courriel, n'obtient pas sa clé.
        key = self._call(bearer=_live_token("sub-jane")).json()["key"]
        resp = self._call({"create": False}, bearer=_live_token("sub-mallory"))
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"error": "identity mismatch"})
        # Le droit dédié peut réépingler (changement de mode de sujet de l'IdP).
        with self.assertRaises(AccessError):
            self._profiles().with_user(self.admin).action_reset_subject()
        self._profiles().with_user(self.officer).action_reset_subject()
        resp = self._call({"create": False}, bearer=_live_token("sub-nouveau"))
        self.assertEqual(resp.json(), {"key": key, "created": False})
        self.assertEqual(self._profiles().idp_subject, "sub-nouveau")

    def test_machine_token_is_refused(self):
        resp = self._call(bearer="bfos-machine abcdef")
        self.assertEqual(resp.status_code, 401)

    def test_get_is_not_served(self):
        with patch(_INTROSPECT, return_value={"email": self.jane.login}), \
                self._with_key():
            resp = self.url_open(_URL, headers={
                "Host": _HOST, "Authorization": f"Bearer {_live_token()}"})
        self.assertNotEqual(resp.status_code, 200)
        self.assertFalse(self._profiles())

    # --- journaux ------------------------------------------------------------
    def test_key_never_reaches_the_logs(self):
        with self.assertLogs("odoo", level="DEBUG") as logs:
            key = self._call({"create": True}).json()["key"]
            self._call({"create": False})
        joined = "\n".join(logs.output)
        self.assertIn("cle de profil remise", joined)
        self.assertIn(self.jane.login, joined)
        self.assertNotIn(key, joined)
        raw = base64.b64decode(key)
        self.assertNotIn(raw.hex(), joined)


@tagged("post_install", "-at_install")
class TestLiveProfileAccess(_Base, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_data()

    def _profile(self):
        with self._with_key():
            key, created = self.env["bf.policy.live.profile"].sudo()._serve_key(
                self.jane, "sub-jane", True)
        self.assertTrue(created)
        return self._profiles(), key

    def test_ordinary_internal_and_portal_read_nothing(self):
        profile, _key = self._profile()
        for user in (self.bob, self.portal, self.jane):
            Profile = self.env["bf.policy.live.profile"].with_user(user)
            with self.assertRaises(AccessError, msg=user.login):
                Profile.search([])
            with self.assertRaises(AccessError, msg=user.login):
                profile.with_user(user).read(["user_id"])

    def test_admin_reads_the_record_never_the_key(self):
        profile, key = self._profile()
        rec = profile.with_user(self.admin)
        data = rec.read()[0]
        self.assertNotIn("key_enc", data)
        self.assertNotIn(key, repr(data))
        with self.assertRaises(AccessError):
            rec.read(["key_enc"])
        with self.assertRaises(AccessError):
            rec.search([("key_enc", "!=", False)])
        self.assertNotIn("key_enc", rec.fields_get())
        # Le sujet Authentik non plus : avec lui, un administrateur qui
        # pointerait l'URL userinfo vers un faux serveur forgerait un jeton.
        with self.assertRaises(AccessError):
            rec.read(["idp_subject"])
        self.assertEqual(profile.with_user(self.officer).idp_subject, "sub-jane")

    def test_onchange_door_is_closed(self):
        # onchange sur la fiche d'autrui : `fetch` vérifie ACL et groups=.
        profile, _key = self._profile()
        spec = {"user_id": {}, "key_enc": {}}
        for user in (self.portal, self.bob, self.admin):
            with self.assertRaises(AccessError, msg=user.login):
                profile.with_user(user).onchange({}, ["user_id"], spec)

    def test_context_defaults_do_not_leak_into_creation(self):
        with self._with_key():
            self.env["bf.policy.live.profile"].sudo().with_context(
                default_revoked=True, default_serve_count=99,
            )._serve_key(self.jane, "sub-jane", True)
        profile = self._profiles()
        self.assertFalse(profile.revoked)
        self.assertEqual(profile.serve_count, 1)

    def test_views_open_for_admin_and_officer(self):
        # Le client web lit tous les champs d'une vue : chacun doit être lisible.
        from lxml import etree
        profile, _key = self._profile()
        for user in (self.admin, self.officer):
            Profile = self.env["bf.policy.live.profile"].with_user(user)
            views = Profile.get_views([(False, "list"), (False, "form")])["views"]
            for kind in ("list", "form"):
                arch = etree.fromstring(views[kind]["arch"])
                names = {n.get("name") for n in arch.iter("field")}
                self.assertNotIn("key_enc", names)
                profile.with_user(user).read(sorted(names))
            org_arch = self.Org.with_user(user).get_views(
                [(False, "form")])["views"]["form"]["arch"]
            self.assertIn("live_oidc_client_id", org_arch)

    def test_no_direct_create_or_write(self):
        profile, _key = self._profile()
        for user in (self.admin, self.officer):
            Profile = self.env["bf.policy.live.profile"].with_user(user)
            with self.assertRaises(AccessError):
                Profile.create({"user_id": self.bob.id, "key_enc": "x"})
            with self.assertRaises(AccessError):
                profile.with_user(user).write({"revoked": False})
            with self.assertRaises(AccessError):
                profile.with_user(user).write({"user_id": self.bob.id})

    def test_service_methods_are_not_rpc_callable(self):
        Profile = self.env["bf.policy.live.profile"].with_user(self.portal)
        for name in ("_serve_key",):
            with self.assertRaises(AccessError):
                get_public_method(Profile, name)
        # Même appelée en Python, hors sudo, elle refuse.
        with self.assertRaises(AccessError):
            self.env["bf.policy.live.profile"].with_user(self.officer)._serve_key(
                self.jane, "sub-jane", True)

    def test_revoke_needs_the_dedicated_right(self):
        profile, _key = self._profile()
        for user in (self.admin, self.bob, self.portal):
            with self.assertRaises(AccessError, msg=user.login):
                profile.with_user(user).action_revoke()
        self.assertFalse(profile.revoked)
        self.assertTrue(profile.sudo().key_enc)
        profile.with_user(self.officer).action_revoke()
        self.assertTrue(profile.revoked)
        self.assertFalse(profile.sudo().key_enc)

    def test_unlink_needs_the_dedicated_right(self):
        profile, _key = self._profile()
        for user in (self.admin, self.bob):
            with self.assertRaises(AccessError, msg=user.login):
                profile.with_user(user).unlink()
        profile.with_user(self.officer).unlink()
        self.assertFalse(profile.exists())

    def test_fail_closed_without_escrow(self):
        env_p, cfg_p = self._without_key()
        with env_p, cfg_p, self.assertRaises(escrow.EscrowUnavailable):
            self.env["bf.policy.live.profile"].sudo()._serve_key(
                self.jane, "sub-jane", True)
        self.assertFalse(self._profiles())

    def test_one_profile_per_person(self):
        from psycopg2 import IntegrityError
        from odoo.tools import mute_logger
        self._profile()
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError), \
                self.env.cr.savepoint():
            self.env["bf.policy.live.profile"].sudo().create(
                {"user_id": self.jane.id})

    def test_live_client_must_differ_from_install_client(self):
        self.assertEqual(self.org.live_oidc_client_id, _LIVE)
        with self.assertRaises(ValidationError):
            self.org.write({"live_oidc_client_id": _INSTALL})
        with self.assertRaises(ValidationError):
            self.org.write({"nextcloud_oidc_client_id": _LIVE})
