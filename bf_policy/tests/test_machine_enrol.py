"""Enrolement d'une machine et re-synchronisation de sa politique.

Ce qui est couvert ici, et pourquoi :

- Le secret n'existe qu'une fois. On verifie qu'Odoo n'en garde que l'empreinte
  et que la meme machine qui rejoue son enrolement fait TOURNER son jeton au
  lieu d'empiler une deuxieme fiche.
- La revocation tient. Une machine archivee ne peut ni se synchroniser, ni se
  re-enroler sous la meme identite — sans cette seconde moitie, revoquer ne
  servirait a rien.
- L'autorisation se rejoue a chaque synchronisation, pas seulement a
  l'installation : sortir quelqu'un du groupe autorise doit couper ses postes.
- Un jeton presente sur le domaine d'un AUTRE locataire est refuse, et refuse
  de la meme facon qu'un jeton inconnu (pas de canal d'information).

Les chemins HTTP authentifies par porteur Authentik ne sont pas rejouables en
test (il faudrait l'IdP) : ils sont couverts au niveau modele, et cote HTTP on
verifie les refus, qui sont justement ce qui doit tenir sans IdP.
"""

from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.bf_policy.controllers.main import _MACHINE_UUID_RE

_URL = "/api/v1/policy/machine"


class MachineCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Machine = cls.env["bf.policy.machine"]
        cls.company = cls.env["res.company"].create({
            "name": "Foxy Inc.", "website": "https://foxy.example",
        })
        cls.org = cls.env["bf.policy.org"].create({
            "company_id": cls.company.id, "domain": "foxy.example",
        })
        cls.user = cls.env["res.users"].create({
            "name": "Riley", "login": "riley@foxy.example",
            "company_id": cls.company.id, "company_ids": [(4, cls.company.id)],
        })


@tagged("post_install", "-at_install")
class TestMachineModel(MachineCase):
    def test_enrol_returns_token_and_stores_only_its_hash(self):
        machine, token = self.Machine._enrol(
            self.org, self.user, "aaaaaaaa-1111-2222-3333-444444444444",
            "bf-riley", os_version="26.07")
        self.assertTrue(machine)
        self.assertTrue(token)
        self.assertNotEqual(machine.token_hash, token,
                            "le secret ne doit pas etre stocke en clair")
        self.assertEqual(machine.token_hash, self.Machine._hash_token(token))
        self.assertEqual(machine.hostname, "bf-riley")
        self.assertEqual(machine.user_id, self.user)
        self.assertEqual(machine.os_version, "26.07")
        self.assertEqual(machine.sync_count, 0)
        self.assertFalse(machine.last_seen)

    def test_reenrol_same_uuid_rotates_token_in_place(self):
        first, token1 = self.Machine._enrol(
            self.org, self.user, "same-uuid-0001", "bf-riley")
        second, token2 = self.Machine._enrol(
            self.org, self.user, "same-uuid-0001", "bf-riley")
        self.assertEqual(first, second, "une seule fiche par UUID")
        self.assertNotEqual(token1, token2)
        # L'ancien jeton meurt avec la rotation.
        self.assertFalse(self.Machine._authenticate(token1))
        self.assertEqual(self.Machine._authenticate(token2), second)

    def test_revoked_machine_cannot_reenrol(self):
        machine, _token = self.Machine._enrol(
            self.org, self.user, "revoked-uuid-01", "bf-riley")
        machine.action_revoke()
        self.assertFalse(machine.active)
        again, token = self.Machine._enrol(
            self.org, self.user, "revoked-uuid-01", "bf-riley")
        self.assertIsNone(again)
        self.assertIsNone(token)

    def test_authenticate_ignores_revoked_and_unknown(self):
        machine, token = self.Machine._enrol(
            self.org, self.user, "auth-uuid-0001", "bf-riley")
        self.assertEqual(self.Machine._authenticate(token), machine)
        self.assertFalse(self.Machine._authenticate("pas-un-jeton"))
        self.assertFalse(self.Machine._authenticate(""))
        machine.action_revoke()
        self.assertFalse(self.Machine._authenticate(token))

    def test_enrol_rejects_blank_uuid(self):
        machine, token = self.Machine._enrol(self.org, self.user, "  ", "bf-riley")
        self.assertIsNone(machine)
        self.assertIsNone(token)

    def test_touch_records_the_sync(self):
        machine, _token = self.Machine._enrol(
            self.org, self.user, "touch-uuid-001", "bf-riley")
        machine._touch("203.0.113.7")
        self.assertTrue(machine.last_seen)
        self.assertEqual(machine.last_seen_ip, "203.0.113.7")
        self.assertEqual(machine.sync_count, 1)
        machine._touch("203.0.113.7")
        self.assertEqual(machine.sync_count, 2)

    def test_hostname_falls_back_when_blank(self):
        machine, _token = self.Machine._enrol(
            self.org, self.user, "hostless-uuid-1", "")
        self.assertEqual(machine.hostname, "blue-fox-os")

    def test_uuid_pattern_bounds(self):
        # Un uuid4 canonique passe ; ce qui n'a pas la forme d'un identifiant
        # est refuse avant d'atteindre l'index SQL.
        self.assertTrue(_MACHINE_UUID_RE.match(
            "5f2b9a1c-7c3e-4b0f-9a2d-1e6f8c4b7d90"))
        self.assertTrue(_MACHINE_UUID_RE.match("bf.machine_01-A"))
        for bad in ("", "court", "-commence-par-un-tiret",
                    "espace dedans 1234", "a" * 65, "point;virgule;12345"):
            self.assertFalse(_MACHINE_UUID_RE.match(bad), bad)


@tagged("post_install", "-at_install")
class TestMachineEndpoint(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Machine = cls.env["bf.policy.machine"]
        Org = cls.env["bf.policy.org"]
        # Neutralise les orgs existantes pour que les hotes ci-dessous
        # resolvent les notres (meme precaution que test_routing).
        Org.search([]).write({"active": False})
        cls.company_a = cls.env["res.company"].create({"name": "Tenant A"})
        # Mode explicite : le defaut des nouvelles fiches est « group », qui
        # sans groupe n'autorise personne.
        cls.org_a = Org.create({
            "company_id": cls.company_a.id, "domain": "a.example",
            "provision_mode": "any"})
        cls.company_b = cls.env["res.company"].create({"name": "Tenant B"})
        cls.org_b = Org.create({
            "company_id": cls.company_b.id, "domain": "b.example"})
        cls.user_a = cls.env["res.users"].create({
            "name": "Ada", "login": "ada@a.example",
            "company_id": cls.company_a.id, "company_ids": [(4, cls.company_a.id)],
        })
        cls.machine, cls.token = cls.Machine._enrol(
            cls.org_a, cls.user_a, "http-uuid-000001", "bf-ada")
        cls.env.flush_all()

    def _get(self, token=None, host="a.example"):
        headers = {"Host": host}
        if token is not None:
            headers["Authorization"] = f"Bearer bfos-machine {token}"
        return self.url_open(_URL, headers=headers)

    def test_without_token_is_401(self):
        resp = self._get()
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"], "machine token required")

    def test_person_bearer_is_not_a_machine_token(self):
        # Un porteur Authentik ordinaire sur /machine : refuse, sans partir
        # valider quoi que ce soit chez l'IdP.
        resp = self.url_open(_URL, headers={
            "Host": "a.example", "Authorization": "Bearer un-porteur-de-personne"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"], "machine token required")

    def test_valid_token_serves_the_policy(self):
        resp = self._get(self.token)
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["schema"], "bf-policy/v2")
        self.assertEqual(payload["user"]["login"], "ada@a.example")
        self.assertIn("apps", payload)
        self.machine.invalidate_recordset()
        self.assertTrue(self.machine.last_seen, "la synchronisation est datee")
        self.assertEqual(self.machine.sync_count, 1)

    def test_unknown_token_is_401(self):
        resp = self._get("un-jeton-invente")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"], "invalid machine token")

    def test_revoked_token_is_401(self):
        machine, token = self.Machine._enrol(
            self.org_a, self.user_a, "http-uuid-revoked", "bf-ada2")
        machine.action_revoke()
        self.env.flush_all()
        resp = self._get(token)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"], "invalid machine token")

    def test_token_of_another_tenant_is_401(self):
        # Meme jeton, servi sur le domaine du locataire B : refuse comme un
        # jeton inconnu, sans dire qu'il est valide ailleurs.
        resp = self._get(self.token, host="b.example")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"], "invalid machine token")

    def test_user_no_longer_authorized_cuts_the_machine(self):
        group = self.env["res.groups"].create({"name": "BFOS Provisioners"})
        self.org_a.write({"provision_mode": "group",
                          "provision_group_ids": [(6, 0, [group.id])]})
        self.env.flush_all()
        resp = self._get(self.token)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"], "user not authorized to provision")
        # Remise dans le groupe : le poste redevient synchronisable.
        self.user_a.groups_id = [(4, group.id)]
        self.env.flush_all()
        self.assertEqual(self._get(self.token).status_code, 200)

    def test_machine_token_sent_to_me_is_named_as_such(self):
        resp = self.url_open("/api/v1/policy/me", headers={
            "Host": "a.example",
            "Authorization": f"Bearer bfos-machine {self.token}"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"],
                         "machine token: use /api/v1/policy/machine")

    def test_enroll_without_authentication_is_401(self):
        resp = self.url_open("/api/v1/policy/enroll", data="{}",
                             headers={"Host": "a.example",
                                      "Content-Type": "application/json"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["error"], "authentication required")
