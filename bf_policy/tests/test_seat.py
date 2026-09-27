"""Shared seats (18.0.2.12.0): a machine that belongs to a profile, not a person.

What must hold, and why:

- A shared seat receives NOBODY's personal settings: not the installer's
  override, not their photo, not their timezone. Thirty people use it.
- Who may log in is exactly the profile's groups plus the machine's
  borrowers, and a name that could smuggle a second entry into sssd.conf
  (comma, space, newline) is refused.
- The owner is a person XOR a profile, enforced in the database.
- Re-enrolment follows the same rules as a personal machine: the same profile
  rotates the token and keeps the name, anything else is a conflict.
- Archiving the profile, or switching the org back to local accounts, stops
  serving its machines at their next sync.
"""
import json
import pathlib

from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.bf_policy.models.bf_policy import EnrolConflict

_LDAP = {
    "login_mode": "sssd",
    "ldap_uri": "ldaps://auth.example.com:636",
    "ldap_base_dn": "DC=example,DC=com",
    "ldap_bind_dn": "cn=svc,ou=users,DC=example,DC=com",
}


class SeatCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Machine = cls.env["bf.policy.machine"]
        cls.Profile = cls.env["bf.policy.seat.profile"]
        cls.company = cls.env["res.company"].create({
            "name": "École Renard", "website": "https://renard.example"})
        cls.org = cls.env["bf.policy.org"].create({
            "company_id": cls.company.id, "domain": "renard.example",
            "provision_mode": "any", "accent_color": "#123456",
            "timezone": "America/Montreal", **_LDAP})
        cls.installer = cls.env["res.users"].create({
            "name": "Tech", "login": "tech@renard.example", "tz": "Pacific/Auckland",
            "company_id": cls.company.id, "company_ids": [(4, cls.company.id)]})
        cls.lab = cls.Profile.create({
            "name": "Library lab", "code": "lab-library", "org_id": cls.org.id,
            "kind": "lab", "login_groups": "students, it-staff",
            "ephemeral_home": True, "offline_login": False, "auto_lock_minutes": 5})
        cls.loan = cls.Profile.create({
            "name": "Loans", "code": "loan", "org_id": cls.org.id, "kind": "loan",
            "hostname_pattern": "pret-{n}", "login_groups": "it-staff",
            "offline_login": True, "offline_max_days": 21})


@tagged("post_install", "-at_install")
class TestSeatProfile(SeatCase):
    def test_code_must_be_a_short_slug(self):
        for bad in ("Lab Library", "x", "-lab", "lab;rm", "a" * 33):
            with self.assertRaises(ValidationError, msg=bad):
                self.Profile.create({"name": "X", "code": bad, "org_id": self.org.id})

    def test_group_names_cannot_smuggle_a_second_entry(self):
        # A newline or a comma inside a name would add a group to sssd.conf.
        # The splitter cuts on them, so what reaches the check is each piece:
        # a piece that is not a plain name is refused.
        with self.assertRaises(ValidationError):
            self.lab.login_groups = "students\nsimple_allow_groups=*"

    def test_hostname_pattern_needs_a_number(self):
        with self.assertRaises(ValidationError):
            self.lab.hostname_pattern = "lab"

    def test_needs_the_directory_login(self):
        company = self.env["res.company"].create({"name": "Local Co"})
        org = self.env["bf.policy.org"].create({
            "company_id": company.id, "domain": "local.example",
            "login_mode": "local"})
        with self.assertRaises(ValidationError):
            self.Profile.create({"name": "X", "code": "lab", "org_id": org.id})

    def test_hostnames_come_from_the_counter(self):
        self.assertEqual(self.lab._take_hostname(), "lab-library-001")
        self.assertEqual(self.lab._take_hostname(), "lab-library-002")
        self.assertEqual(self.loan._take_hostname(), "pret-001")
        self.assertEqual(self.lab._hostname_preview(), "lab-library-003")


@tagged("post_install", "-at_install")
class TestSeatEnrol(SeatCase):
    def _enrol(self, uuid, profile=None, user=None):
        return self.Machine._enrol(
            self.org, user or self.installer, uuid, "ignored-by-a-seat",
            seat_profile=profile if profile is not None else self.lab)

    def test_enrolled_for_the_profile_not_the_installer(self):
        machine, token = self._enrol("seat-uuid-0001")
        self.assertTrue(token)
        self.assertFalse(machine.user_id)
        self.assertEqual(machine.seat_profile_id, self.lab)
        self.assertEqual(machine.enrolled_by_id, self.installer)
        self.assertEqual(machine.hostname, "lab-library-001",
                         "the name comes from the profile, not from the client")

    def test_reenrol_same_profile_rotates_token_and_keeps_name(self):
        first, token1 = self._enrol("seat-uuid-0002")
        second, token2 = self._enrol("seat-uuid-0002")
        self.assertEqual(first, second)
        self.assertEqual(second.hostname, "lab-library-001")
        self.assertFalse(self.Machine._authenticate(token1))
        self.assertEqual(self.Machine._authenticate(token2), second)
        self.assertEqual(self.lab.next_number, 2, "a replay takes no number")

    def test_reenrol_under_another_profile_is_a_conflict(self):
        self._enrol("seat-uuid-0003")
        with mute_logger("odoo.addons.bf_policy.models.bf_policy"), \
                self.assertRaises(EnrolConflict):
            self._enrol("seat-uuid-0003", profile=self.loan)

    def test_personal_machine_cannot_become_a_seat_and_back(self):
        self.Machine._enrol(self.org, self.installer, "seat-uuid-0004", "bf-tech")
        with mute_logger("odoo.addons.bf_policy.models.bf_policy"), \
                self.assertRaises(EnrolConflict):
            self._enrol("seat-uuid-0004")
        self._enrol("seat-uuid-0005")
        with mute_logger("odoo.addons.bf_policy.models.bf_policy"), \
                self.assertRaises(EnrolConflict):
            self.Machine._enrol(self.org, self.installer, "seat-uuid-0005", "bf-tech")

    def test_only_its_installer_or_an_admin_replays_a_seat(self):
        machine, token = self._enrol("seat-uuid-0011")
        other = self.env["res.users"].create({
            "name": "Other tech", "login": "tech2@renard.example",
            "company_id": self.company.id, "company_ids": [(4, self.company.id)]})
        with mute_logger("odoo.addons.bf_policy.models.bf_policy"), \
                self.assertRaises(EnrolConflict):
            self._enrol("seat-uuid-0011", user=other)
        self.assertEqual(self.Machine._authenticate(token), machine, "token untouched")
        admin = self.env.ref("base.user_admin")
        again, token2 = self._enrol("seat-uuid-0011", user=admin)
        self.assertEqual(again, machine)
        self.assertEqual(again.enrolled_by_id, admin)

    def test_archived_profile_takes_no_machine(self):
        self.lab.active = False
        with self.assertRaises(EnrolConflict):
            self._enrol("seat-uuid-0006")

    def test_profile_of_another_org_is_refused(self):
        company = self.env["res.company"].create({"name": "Other school"})
        org = self.env["bf.policy.org"].create({
            "company_id": company.id, "domain": "other.example", **_LDAP})
        with self.assertRaises(EnrolConflict):
            self.Machine._enrol(org, self.installer, "seat-uuid-0007", "x",
                                seat_profile=self.lab)

    def test_owner_is_a_person_xor_a_profile(self):
        machine, _token = self._enrol("seat-uuid-0008")
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError), \
                self.env.cr.savepoint():
            machine.write({"user_id": self.installer.id})
            self.env.flush_all()
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError), \
                self.env.cr.savepoint():
            machine.write({"seat_profile_id": False})
            self.env.flush_all()

    def test_borrowers_only_on_a_shared_seat(self):
        personal, _token = self.Machine._enrol(
            self.org, self.installer, "seat-uuid-0009", "bf-tech")
        with self.assertRaises(ValidationError):
            personal.seat_allowed_users = "e0001"
        seat, _token = self._enrol("seat-uuid-0010", profile=self.loan)
        seat.seat_allowed_users = "e0001"
        with self.assertRaises(ValidationError):
            seat.seat_allowed_users = "e0001 ro$ot"


@tagged("post_install", "-at_install")
class TestSeatPolicy(SeatCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The installer's personal settings: none of it may reach a seat.
        cls.env["bf.policy.user"].create({
            "user_id": cls.installer.id, "company_id": cls.company.id,
            "accent_color": "#ff00ff", "locale": "en_NZ.UTF-8"})
        cls.app_org = cls.env["bf.policy.app"].create({"flatpak_id": "org.gimp.GIMP"})
        cls.app_seat = cls.env["bf.policy.app"].create({"flatpak_id": "org.kde.kturtle"})
        cls.org.app_ids = [(6, 0, cls.app_org.ids)]
        cls.lab.app_ids = [(6, 0, cls.app_seat.ids)]
        cls.machine, cls.token = cls.Machine._enrol(
            cls.org, cls.installer, "seat-policy-0001", "x", seat_profile=cls.lab)

    def _payload(self, machine=None, profile=None):
        return self.org.get_policy_json(
            self.installer, seat_profile=profile or self.lab,
            machine=machine if machine is not None else self.machine)

    def test_nobody_personal_on_a_seat(self):
        payload = self._payload()
        self.assertEqual(payload["user"], {"login": ""})
        self.assertEqual(payload["session"]["accent_color"], "#123456")
        self.assertEqual(payload["install"]["locale"], "fr_CA.UTF-8")
        self.assertEqual(payload["install"]["timezone"], "America/Montreal",
                         "not the installer's Auckland")
        self.assertIsNone(payload["session"]["clock"]["second_timezone"])

    def test_who_logs_in(self):
        login = self._payload()["install"]["login"]
        self.assertEqual(login["allow_groups"], ["students", "it-staff"])
        self.assertEqual(login["allow_users"], [])
        self.assertTrue(login["deny_if_empty"])
        self.assertTrue(login["bind_dn"], "the seat still needs the directory")

    def test_borrower_is_served(self):
        machine, _token = self.Machine._enrol(
            self.org, self.installer, "seat-policy-0002", "x", seat_profile=self.loan)
        machine.seat_allowed_users = "e0042"
        payload = self._payload(machine=machine, profile=self.loan)
        self.assertEqual(payload["install"]["login"]["allow_users"], ["e0042"])
        self.assertEqual(payload["install"]["login"]["allow_groups"], ["it-staff"])
        self.assertEqual(payload["seat"]["borrowers"], ["e0042"])
        self.assertEqual(payload["seat"]["kind"], "loan")
        self.assertEqual(payload["install"]["hostname"], "pret-001")
        self.assertEqual(payload["policies"]["offline_login"],
                         {"enabled": True, "max_offline_days": 21})

    def test_profile_settings_win(self):
        payload = self._payload()
        self.assertEqual(payload["install"]["hostname"], "lab-library-001")
        self.assertEqual(payload["policies"]["auto_lock_minutes"], 5)
        self.assertFalse(payload["policies"]["offline_login"]["enabled"])
        self.assertTrue(payload["seat"]["ephemeral_home"])
        self.assertEqual(payload["apps"]["install"],
                         ["org.gimp.GIMP", "org.kde.kturtle"])

    def test_seat_removal_wins_over_org_install(self):
        self.lab.app_remove_ids = [(6, 0, self.app_org.ids)]
        apps = self._payload()["apps"]
        self.assertEqual(apps["install"], ["org.kde.kturtle"])
        self.assertIn("org.gimp.GIMP", apps["remove"])

    def test_preview_without_machine(self):
        payload = self.org.get_policy_json(self.installer, seat_profile=self.lab)
        self.assertEqual(payload["install"]["hostname"], "lab-library-002")
        self.assertEqual(payload["install"]["login"]["allow_users"], [])

    def test_personal_payload_unchanged(self):
        payload = self.org.get_policy_json(self.installer)
        self.assertNotIn("seat", payload)
        self.assertNotIn("allow_groups", payload["install"]["login"])
        self.assertEqual(payload["user"]["login"], "tech@renard.example")
        self.assertEqual(payload["session"]["accent_color"], "#ff00ff")

    def test_seat_payload_validates_against_the_schema(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        schema_path = (pathlib.Path(__file__).resolve().parent.parent
                       / "static/schema/policy.v2.json")
        jsonschema.validate(self._payload(), json.loads(schema_path.read_text()))


@tagged("post_install", "-at_install")
class TestSeatEndpoint(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Org = cls.env["bf.policy.org"]
        Org.search([]).write({"active": False})
        cls.company = cls.env["res.company"].create({"name": "École HTTP"})
        cls.org = Org.create({
            "company_id": cls.company.id, "domain": "seat.example",
            "provision_mode": "any", **_LDAP})
        cls.installer = cls.env["res.users"].create({
            "name": "Tech", "login": "tech@seat.example",
            "company_id": cls.company.id, "company_ids": [(4, cls.company.id)]})
        cls.profile = cls.env["bf.policy.seat.profile"].create({
            "name": "Lab", "code": "lab", "org_id": cls.org.id,
            "login_groups": "students"})
        cls.machine, cls.token = cls.env["bf.policy.machine"]._enrol(
            cls.org, cls.installer, "seat-http-0001", "x", seat_profile=cls.profile)
        cls.env.flush_all()

    def _get(self):
        return self.url_open("/api/v1/policy/machine", headers={
            "Host": "seat.example",
            "Authorization": f"Bearer bfos-machine {self.token}"})

    def test_seat_machine_gets_the_profile_policy(self):
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["seat"]["profile"], "lab")
        self.assertEqual(payload["user"], {"login": ""})
        self.assertEqual(payload["install"]["login"]["allow_groups"], ["students"])
        self.machine.invalidate_recordset()
        self.assertEqual(self.machine.sync_count, 1)

    def test_archived_profile_cuts_its_machines(self):
        self.profile.active = False
        self.env.flush_all()
        with mute_logger("odoo.addons.bf_policy.controllers.main"):
            resp = self._get()
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"], "seat profile inactive")

    def test_back_to_local_accounts_cuts_its_machines(self):
        self.org.login_mode = "local"
        self.env.flush_all()
        with mute_logger("odoo.addons.bf_policy.controllers.main"):
            resp = self._get()
        self.assertEqual(resp.status_code, 403)

    def test_installer_losing_rights_does_not_cut_a_seat(self):
        # The installer is not the owner: their departure must not black out
        # a lab they happened to install.
        self.installer.active = False
        self.env.flush_all()
        self.assertEqual(self._get().status_code, 200)

    def test_me_with_unknown_seat_needs_authentication_first(self):
        # No bearer: refused before the seat code is even looked at, so the
        # endpoint says nothing about which profile codes exist.
        resp = self.url_open("/api/v1/policy/me?seat=nope",
                             headers={"Host": "seat.example"})
        self.assertEqual(resp.status_code, 401)
