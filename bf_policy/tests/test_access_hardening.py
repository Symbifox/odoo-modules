"""Qui obtient une politique, qui enrole un poste, qui lit une phrase de disque.

Chaque classe garde un refus :

- TestWhoIsAuthorized : un usager portail, archive ou etranger a la societe
  de l'organisation n'est autorise dans AUCUN mode, « any » compris ; une
  nouvelle organisation n'autorise personne tant qu'on ne l'a pas decide.
- TestBearerIdentity : le porteur se rattache par UNE revendication comparee
  au login, jamais au champ courriel, et plusieurs usagers possibles valent
  refus. La session Odoo n'ouvre ni /me ni /enroll.
- TestEnrolTakeover : connaitre l'UUID d'un poste ne permet ni de se le faire
  reassigner, ni de faire tourner son jeton, ni de remplacer sa phrase de
  disque en sequestre.
- TestRevealOnlyThroughTheButton : la phrase ne se lit que par le bouton qui
  compte et attribue la lecture.
- TestPrivateMethods : les methodes de service ne s'appellent pas par RPC.
- TestMachineSyncReplaysTheCheck : la synchro d'un poste rejoue ces refus.
- TestLdapSecretOnlyForSssd : le mot de passe de liaison ne part qu'en sssd.

Le point userinfo du fournisseur d'identite est remplace par un double : les
essais HTTP exercent donc le vrai chemin du controleur, jeton compris, sans IdP.
"""

import json
import os
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.service.model import get_public_method
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.bf_policy.models import escrow
from odoo.addons.bf_policy.models.bf_policy import EnrolConflict

_INTROSPECT = "odoo.addons.bf_policy.controllers.main._introspect"
_HOST = "doe.example.test"
_PHRASE = "JDOE1-4KX7M-Q2RTB-9HJVC-ZE6YA"
_OTHER_PHRASE = "ROE22-AAAAA-BBBBB-CCCCC-DDDDD"
_PASSWORD = "Essai-Jane-Doe-2026!"


def _a_key():
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def _users(env, company, *specs):
    """specs : (nom, login, groupe xmlid[, courriel]) — tous dans `company`."""
    out = []
    for spec in specs:
        name, login, group = spec[:3]
        vals = {
            "name": name, "login": login, "password": _PASSWORD,
            "company_id": company.id, "company_ids": [(6, 0, [company.id])],
            "groups_id": [(6, 0, [env.ref(group).id])],
        }
        if len(spec) > 3:
            vals["email"] = spec[3]
        out.append(env["res.users"].with_context(no_reset_password=True).create(vals))
    return out


@tagged("post_install", "-at_install")
class TestWhoIsAuthorized(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Doe Test Co."})
        cls.other_company = cls.env["res.company"].create({"name": "Roe Test Co."})
        cls.org = cls.env["bf.policy.org"].create({
            "company_id": cls.company.id, "domain": _HOST,
            "provision_mode": "any"})
        cls.internal, cls.portal = _users(
            cls.env, cls.company,
            ("Jane Doe", "jane.doe@example.test", "base.group_user"),
            ("John Doe", "john.doe@example.test", "base.group_portal"))
        (cls.outsider,) = _users(
            cls.env, cls.other_company,
            ("Richard Roe", "richard.roe@example.test", "base.group_user"))

    def test_internal_user_of_the_company_is_authorized_in_any_mode(self):
        self.assertTrue(self.org.is_user_authorized(self.internal))

    def test_portal_user_is_refused_in_every_mode(self):
        self.assertTrue(self.portal.share)
        grp = self.env["res.groups"].create({"name": "Doe provisioners"})
        self.portal.sudo().write({"groups_id": [(4, grp.id)]})
        for mode, vals in (("any", {}),
                           ("group", {"provision_group_ids": [(6, 0, [grp.id])]}),
                           ("allowlist", {"provision_user_ids": [(6, 0, [self.portal.id])]})):
            self.org.write(dict(vals, provision_mode=mode))
            self.assertFalse(self.org.is_user_authorized(self.portal), mode)

    def test_archived_user_is_refused(self):
        self.internal.active = False
        self.assertFalse(self.org.is_user_authorized(self.internal))

    def test_user_outside_the_company_is_refused(self):
        self.assertFalse(self.org.is_user_authorized(self.outsider))

    def test_new_org_defaults_to_group_and_authorizes_nobody(self):
        company = self.env["res.company"].create({"name": "Fresh Test Co."})
        org = self.env["bf.policy.org"].create({"company_id": company.id})
        self.assertEqual(org.provision_mode, "group")
        (user,) = _users(self.env, company,
                         ("Jane Roe", "jane.roe@example.test", "base.group_user"))
        self.assertFalse(org.is_user_authorized(user))


class _HttpBase(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Org = cls.env["bf.policy.org"]
        cls.Org.search([]).write({"active": False})
        cls.company = cls.env["res.company"].create({"name": "Doe Test Co."})
        cls.org = cls.Org.create({
            "company_id": cls.company.id, "domain": _HOST,
            "provision_mode": "any", "disk_escrow": True,
            "authentik_userinfo_url": "https://idp.example.test/userinfo/"})
        cls.key = _a_key()

    def _with_key(self):
        return patch.dict(os.environ, {escrow.ENV_VAR: self.key})

    def _call(self, path, claims=None, body=None, bearer="jeton-essai"):
        headers = {"Host": _HOST}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        if body is not None:
            headers["Content-Type"] = "application/json"
        self.env.flush_all()
        with patch(_INTROSPECT, return_value=claims), self._with_key():
            if body is None:
                resp = self.url_open(path, headers=headers)
            else:
                resp = self.url_open(path, data=json.dumps(body), headers=headers)
        self.env.invalidate_all()
        return resp


@tagged("post_install", "-at_install")
class TestBearerIdentity(_HttpBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.jane, cls.portal, cls.alex = _users(
            cls.env, cls.company,
            ("Jane Doe", "jane.doe@example.test", "base.group_user"),
            ("John Doe", "john.doe@example.test", "base.group_portal"),
            # Nomme pour se trier AVANT Jane : l'ancien `limit=1` le choisissait.
            ("Alex Doe", "jdoe", "base.group_user", "alex.doe@example.test"))
        cls.env.flush_all()

    def test_internal_bearer_gets_its_own_policy(self):
        resp = self._call("/api/v1/policy/me", {"email": "jane.doe@example.test"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["user"]["login"], "jane.doe@example.test")

    def test_portal_bearer_is_refused_on_me(self):
        resp = self._call("/api/v1/policy/me", {"email": "john.doe@example.test"})
        self.assertEqual(resp.status_code, 403)
        self.assertNotIn("install", resp.json())

    def test_portal_bearer_is_refused_on_enroll(self):
        resp = self._call("/api/v1/policy/enroll",
                          {"email": "john.doe@example.test"},
                          body={"machine_uuid": "portal-uuid-0001",
                                "hostname": "bf-john"})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(self.env["bf.policy.machine"].with_context(
            active_test=False).search([("machine_uuid", "=", "portal-uuid-0001")]))

    def test_odoo_session_no_longer_opens_me_or_enroll(self):
        self.authenticate("john.doe@example.test", _PASSWORD)
        resp = self.url_open("/api/v1/policy/me", headers={"Host": _HOST})
        self.assertEqual(resp.status_code, 401)
        resp = self.url_open("/api/v1/policy/enroll", headers={
            "Host": _HOST, "Content-Type": "application/json"},
            data=json.dumps({"machine_uuid": "session-uuid-001"}))
        self.assertEqual(resp.status_code, 401)

    def test_the_email_field_is_not_an_identity(self):
        # Alex porte le courriel alex.doe@… mais son login est « jdoe ».
        resp = self._call("/api/v1/policy/me", {"email": "alex.doe@example.test"})
        self.assertEqual(resp.status_code, 401)

    def test_only_the_configured_claim_is_read(self):
        # preferred_username designe Alex, email designe Jane : seule la
        # revendication configuree (email) compte.
        resp = self._call("/api/v1/policy/me", {
            "preferred_username": "jdoe", "email": "jane.doe@example.test"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["user"]["login"], "jane.doe@example.test")

    def test_several_matching_users_is_a_refusal(self):
        _users(self.env, self.company,
               ("Jane Case", "jane.case@example.test", "base.group_user"),
               ("Jane Case Bis", "placeholder@example.test", "base.group_user"))
        # Deux logins qui ne different que par la casse.
        self.env.cr.execute(
            "UPDATE res_users SET login = 'Jane.Case@example.test' "
            "WHERE login = 'placeholder@example.test'")
        self.env.flush_all()
        resp = self._call("/api/v1/policy/me", {"email": "JANE.CASE@example.test"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"], "identity matches several users")

    def test_wildcards_in_a_claim_match_nothing(self):
        resp = self._call("/api/v1/policy/me", {"email": "jane_doe@example.test"})
        self.assertEqual(resp.status_code, 401)
        resp = self._call("/api/v1/policy/me", {"email": "%@example.test"})
        self.assertEqual(resp.status_code, 401)

    def test_audience_check_is_off_by_default_and_enforced_when_on(self):
        self.assertFalse(self.org.token_audience_check)
        import base64

        def jwt(claims):
            body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode()
            return "e30." + body.rstrip("=") + ".sig"

        claims = {"email": "jane.doe@example.test"}
        foreign = jwt({"aud": "another-client", "azp": "another-client"})
        self.assertEqual(
            self._call("/api/v1/policy/me", claims, bearer=foreign).status_code, 200)
        self.org.write({"token_audience_check": True, "oidc_client_id": "install-client"})
        self.env.flush_all()
        self.assertEqual(
            self._call("/api/v1/policy/me", claims, bearer=foreign).status_code, 401)
        self.assertEqual(
            self._call("/api/v1/policy/me", claims, bearer="opaque").status_code, 401)
        own = jwt({"aud": "install-client", "azp": "install-client"})
        self.assertEqual(
            self._call("/api/v1/policy/me", claims, bearer=own).status_code, 200)


@tagged("post_install", "-at_install")
class TestEnrolTakeover(_HttpBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.jane, cls.richard = _users(
            cls.env, cls.company,
            ("Jane Doe", "jane.doe@example.test", "base.group_user"),
            ("Richard Roe", "richard.roe@example.test", "base.group_user"))
        cls.Machine = cls.env["bf.policy.machine"]
        cls.machine, cls.token = cls.Machine._enrol(
            cls.org, cls.jane, "jane-uuid-000001", "bf-jane")
        cls.env.flush_all()

    def setUp(self):
        super().setUp()
        with self._with_key():
            ok, _reason = self.machine._escrow_disk_passphrase(_PHRASE)
        self.assertTrue(ok)
        self.env.flush_all()

    def _assert_untouched(self):
        self.machine.invalidate_recordset()
        self.assertEqual(self.machine.user_id, self.jane)
        self.assertEqual(self.Machine._authenticate(self.token), self.machine)
        with self._with_key():
            self.assertEqual(self.machine._read_disk_passphrase(), _PHRASE)

    def test_model_refuses_another_users_reenrolment(self):
        with self.assertRaises(EnrolConflict):
            self.Machine._enrol(self.org, self.richard, "jane-uuid-000001", "bf-richard")
        self._assert_untouched()

    def test_model_refuses_another_orgs_reenrolment(self):
        company = self.env["res.company"].create({"name": "Roe Test Co."})
        org = self.Org.create({"company_id": company.id, "domain": "roe.example.test",
                               "provision_mode": "any"})
        with self.assertRaises(EnrolConflict):
            self.Machine._enrol(org, self.jane, "jane-uuid-000001", "bf-jane")
        self._assert_untouched()

    def test_http_takeover_is_409_and_the_escrow_is_kept(self):
        resp = self._call("/api/v1/policy/enroll",
                          {"email": "richard.roe@example.test"},
                          body={"machine_uuid": "jane-uuid-000001",
                                "hostname": "bf-richard",
                                "disk_passphrase": _OTHER_PHRASE})
        self.assertEqual(resp.status_code, 409)
        self.assertNotIn("token", resp.json())
        self._assert_untouched()

    def test_owner_replay_rotates_the_token_but_keeps_the_escrow(self):
        resp = self._call("/api/v1/policy/enroll",
                          {"email": "jane.doe@example.test"},
                          body={"machine_uuid": "jane-uuid-000001",
                                "hostname": "bf-jane",
                                "disk_passphrase": _OTHER_PHRASE})
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertFalse(payload["disk_escrowed"],
                         "l'installateur doit retomber sur la saisie manuelle")
        self.assertEqual(self.Machine._authenticate(payload["token"]), self.machine)
        with self._with_key():
            self.assertEqual(self.machine._read_disk_passphrase(), _PHRASE)

    def test_a_fresh_uuid_still_enrols_and_escrows(self):
        resp = self._call("/api/v1/policy/enroll",
                          {"email": "jane.doe@example.test"},
                          body={"machine_uuid": "jane-uuid-000002",
                                "hostname": "bf-jane",
                                "disk_passphrase": _OTHER_PHRASE})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["disk_escrowed"])


@tagged("post_install", "-at_install")
class TestRevealOnlyThroughTheButton(_HttpBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = cls.env.ref("bf_policy.group_disk_escrow_read")
        # Administrateurs (seuls a voir les fiches de postes) ET porteurs du
        # droit de revelation : le cas le plus favorable a un contournement.
        cls.jane, cls.richard = _users(
            cls.env, cls.company,
            ("Jane Doe", "jane.doe@example.test", "base.group_system"),
            ("Richard Roe", "richard.roe@example.test", "base.group_system"))
        (cls.jane | cls.richard).write({"groups_id": [(4, group.id)]})
        cls.machine, _token = cls.env["bf.policy.machine"]._enrol(
            cls.org, cls.jane, "reveal-uuid-0001", "bf-jane")

    def setUp(self):
        super().setUp()
        with self._with_key():
            self.machine._escrow_disk_passphrase(_PHRASE)

    def _count(self):
        self.machine.invalidate_recordset()
        return self.machine.disk_reveal_count

    def test_creating_the_screen_directly_is_refused(self):
        Reveal = self.env["bf.policy.machine.reveal"].with_user(self.jane)
        with self.assertRaises(AccessError):
            Reveal.create({"machine_id": self.machine.id})
        # Le contexte du bouton passe par RPC ; il ne suffit pas.
        with self.assertRaises(AccessError):
            Reveal.with_context(bf_policy_reveal_from_button=True).create(
                {"machine_id": self.machine.id})
        self.assertEqual(self._count(), 0)

    def test_the_button_still_reveals_and_counts(self):
        with self._with_key():
            action = self.machine.with_user(self.jane).action_reveal_passphrase()
            wizard = self.env["bf.policy.machine.reveal"].with_user(
                self.jane).browse(action["res_id"])
            self.assertEqual(wizard.passphrase, _PHRASE)
        self.assertEqual(self._count(), 1)

    def test_a_colleague_cannot_read_someone_elses_screen(self):
        with self._with_key():
            action = self.machine.with_user(self.jane).action_reveal_passphrase()
            wizard = self.env["bf.policy.machine.reveal"].with_user(
                self.richard).browse(action["res_id"])
            with self.assertRaises(AccessError):
                wizard.read(["passphrase"])
        self.assertEqual(self.machine.disk_last_revealed_by, self.jane)

    def test_an_old_screen_no_longer_shows_the_passphrase(self):
        with self._with_key():
            action = self.machine.with_user(self.jane).action_reveal_passphrase()
            self.env.cr.execute(
                "UPDATE bf_policy_machine_reveal SET create_date = %s WHERE id = %s",
                (fields.Datetime.now() - timedelta(minutes=10), action["res_id"]))
            wizard = self.env["bf.policy.machine.reveal"].with_user(
                self.jane).browse(action["res_id"])
            wizard.invalidate_recordset()
            self.assertFalse(wizard.passphrase)

    def test_json_rpc_create_is_refused(self):
        self.authenticate("jane.doe@example.test", _PASSWORD)
        with self._with_key():
            resp = self.url_open(
                "/web/dataset/call_kw/bf.policy.machine.reveal/create",
                data=json.dumps({"jsonrpc": "2.0", "method": "call", "id": 1,
                                 "params": {"model": "bf.policy.machine.reveal",
                                            "method": "create",
                                            "args": [{"machine_id": self.machine.id}],
                                            "kwargs": {}}}),
                headers={"Content-Type": "application/json"})
        body = resp.json()
        self.assertIn("error", body)
        self.assertNotIn(_PHRASE, resp.text)
        self.assertEqual(self._count(), 0)

    def _rpc(self, model, method, args, kwargs=None):
        return self.url_open(
            f"/web/dataset/call_kw/{model}/{method}",
            data=json.dumps({"jsonrpc": "2.0", "method": "call", "id": 1,
                             "params": {"model": model, "method": method,
                                        "args": args, "kwargs": kwargs or {}}}),
            headers={"Content-Type": "application/json"}).json()

    def test_the_button_journey_through_the_web_client(self):
        """Le parcours de l'ecran : bouton, puis lecture de l'ecran ouvert."""
        self.authenticate("jane.doe@example.test", _PASSWORD)
        self.env.flush_all()
        with self._with_key():
            action = self._rpc("bf.policy.machine", "action_reveal_passphrase",
                               [[self.machine.id]])["result"]
            shown = self._rpc("bf.policy.machine.reveal", "web_read",
                              [[action["res_id"]]],
                              {"specification": {"passphrase": {}}})["result"]
        self.assertEqual(shown[0]["passphrase"], _PHRASE)
        self.env.invalidate_all()
        self.assertEqual(self._count(), 1)

    def test_the_machine_record_never_carries_the_clear_passphrase(self):
        fields_seen = self.env["bf.policy.machine"].with_user(self.jane).fields_get()
        self.assertNotIn("passphrase", fields_seen)
        data = self.machine.with_user(self.jane).read()[0]
        self.assertNotIn(_PHRASE, json.dumps(data, default=str))
        exported = self.machine.with_user(self.jane).export_data(
            ["disk_passphrase_enc", "hostname"])["datas"]
        self.assertNotIn(_PHRASE, json.dumps(exported, default=str))


@tagged("post_install", "-at_install")
class TestPrivateMethods(TransactionCase):
    def test_service_methods_are_not_callable_over_rpc(self):
        for model, name in (("bf.policy.org", "get_policy_json"),
                            ("bf.policy.org", "is_user_authorized"),
                            ("bf.policy.app", "action_sync_flathub")):
            with self.assertRaises(AccessError, msg=f"{model}.{name}"):
                get_public_method(self.env[model], name)

    def test_buttons_stay_callable(self):
        for name in ("action_reveal_passphrase", "action_forget_passphrase",
                     "action_revoke"):
            self.assertTrue(get_public_method(self.env["bf.policy.machine"], name))


@tagged("post_install", "-at_install")
class TestMachineSyncReplaysTheCheck(_HttpBase):
    """La synchro d'un poste rejoue l'autorisation de son usager, durcie."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.jane, cls.john = _users(
            cls.env, cls.company,
            ("Jane Doe", "jane.doe@example.test", "base.group_user"),
            ("John Doe", "john.doe@example.test", "base.group_portal"))
        Machine = cls.env["bf.policy.machine"]
        cls.m_jane, cls.t_jane = Machine._enrol(
            cls.org, cls.jane, "jane-uuid-000002", "bf-jane")
        cls.m_john, cls.t_john = Machine._enrol(
            cls.org, cls.john, "john-uuid-000002", "bf-john")
        cls.env.flush_all()

    def _sync(self, token):
        return self._call("/api/v1/policy/machine", bearer=f"bfos-machine {token}")

    def test_an_internal_users_machine_still_syncs(self):
        self.assertEqual(self._sync(self.t_jane).status_code, 200)

    def test_a_portal_users_machine_is_cut_off(self):
        self.assertEqual(self._sync(self.t_john).status_code, 403)

    def test_archiving_the_owner_cuts_the_machine_off(self):
        self.jane.active = False
        self.assertEqual(self._sync(self.t_jane).status_code, 403)


@tagged("post_install", "-at_install")
class TestLdapSecretOnlyForSssd(TransactionCase):
    """Le compte de service de l'annuaire ne part qu'aux postes en mode sssd."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Doe Test Co."})
        cls.org = cls.env["bf.policy.org"].create({
            "company_id": cls.company.id, "domain": _HOST,
            "provision_mode": "any", "login_mode": "local",
            "ldap_uri": "ldaps://ldap.example.test:636",
            "ldap_bind_dn": "cn=svc,ou=users,DC=example,DC=test"})
        (cls.jane,) = _users(
            cls.env, cls.company,
            ("Jane Doe", "jane.doe@example.test", "base.group_user"))
        cls.key = _a_key()

    def _login_block(self):
        with patch.dict(os.environ, {escrow.ENV_VAR: self.key}):
            self.org.ldap_bind_password = "mdp-essai-annuaire"
            return self.org.get_policy_json(self.jane)["install"]["login"]

    def test_local_mode_does_not_receive_the_bind_password(self):
        self.assertEqual(self._login_block()["bind_password"], "")

    def test_sssd_mode_still_receives_it(self):
        self.org.login_mode = "sssd"
        self.assertEqual(self._login_block()["bind_password"], "mdp-essai-annuaire")
