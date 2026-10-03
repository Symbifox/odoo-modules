"""La synchro d'une machine ne reçoit pas le mot de passe de liaison LDAP."""
import os
from unittest.mock import patch

from odoo.tests import HttpCase, tagged

from odoo.addons.bf_policy.models import escrow

_MDP = "jeton-de-liaison-factice"


@tagged("post_install", "-at_install")
class TestMachineSansLiaison(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from cryptography.fernet import Fernet
        cls.key = Fernet.generate_key().decode()
        Org = cls.env["bf.policy.org"]
        Org.search([]).write({"active": False})
        company = cls.env["res.company"].create({"name": "Tenant L"})
        cls.org = Org.create({
            "company_id": company.id, "domain": "l.example", "provision_mode": "any",
            "login_mode": "sssd", "ldap_uri": "ldaps://ldap.l.example",
            "ldap_bind_dn": "cn=liaison,dc=l,dc=example"})
        with patch.dict(os.environ, {escrow.ENV_VAR: cls.key}):
            cls.org.ldap_bind_password = _MDP
        user = cls.env["res.users"].create({
            "name": "Lia", "login": "lia@l.example",
            "company_id": company.id, "company_ids": [(4, company.id)]})
        cls.machine, cls.token = cls.env["bf.policy.machine"]._enrol(
            cls.org, user, "http-uuid-sans-liaison", "bf-lia")
        cls.env.flush_all()

    def test_la_synchro_ne_porte_pas_le_mot_de_passe(self):
        with patch.dict(os.environ, {escrow.ENV_VAR: self.key}):
            self.assertEqual(self.org._read_ldap_bind_password(), _MDP)  # prémisse
            resp = self.url_open("/api/v1/policy/machine", headers={
                "Host": "l.example", "Authorization": f"Bearer bfos-machine {self.token}"})
        self.assertEqual(resp.status_code, 200)
        login = resp.json()["install"]["login"]
        self.assertEqual(login["ldap_uri"], "ldaps://ldap.l.example")
        self.assertFalse(login.get("bind_password"))
        self.assertNotIn(_MDP, resp.text)
