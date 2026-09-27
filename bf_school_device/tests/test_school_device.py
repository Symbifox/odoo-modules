"""Students' computers: directory accounts and loans.

The directory is never called: `_directory_call` is replaced by an in-memory
Authentik that records every request, so the tests read what WOULD have left.
"""
from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.bf_school_device.models.account import kid_password
from odoo.addons.bf_school_device.models.school import directory_slug

_LDAP = {
    "login_mode": "sssd",
    "ldap_uri": "ldaps://auth.renard.example:636",
    "ldap_base_dn": "DC=renard,DC=example",
    "ldap_bind_dn": "cn=svc,ou=users,DC=renard,DC=example",
}


class FakeAuthentik:
    """Just enough of /api/v3/core to hold users and groups."""

    def __init__(self):
        self.calls = []
        self.users = {}
        self.groups = {}
        self.passwords = {}
        self.fail_for = set()

    def __call__(self, school, method, path, payload=None):
        self.calls.append((method, path, payload))
        if payload and payload.get("username") in self.fail_for:
            raise UserError("Directory refused POST (400): boom")
        if path.startswith("/api/v3/core/groups/?name="):
            name = path.split("=", 1)[1]
            return {"results": [{"pk": self.groups[name]}] if name in self.groups else []}
        if path == "/api/v3/core/groups/":
            pk = "g-%s" % payload["name"]
            self.groups[payload["name"]] = pk
            return {"pk": pk}
        if path.startswith("/api/v3/core/users/?username="):
            name = path.split("=", 1)[1]
            return {"results": [{"pk": pk, "path": u.get("path")}
                                for pk, u in self.users.items() if u["username"] == name]}
        if path == "/api/v3/core/users/" and method == "POST":
            pk = len(self.users) + 100
            self.users[pk] = dict(payload)
            return {"pk": pk}
        if path.endswith("/set_password/"):
            self.passwords[int(path.split("/")[5])] = payload["password"]
            return {}
        if method == "PATCH":
            self.users[int(path.split("/")[5])].update(payload)
            return {}
        raise AssertionError("unexpected call %s %s" % (method, path))


@tagged("post_install", "-at_install")
class TestSchoolDevice(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.school = env["bf.school"].create({"name": "École du Renard"})
        cls.school.directory_url = "https://auth.renard.example"
        # After the URL: a new URL drops the token it was set for.
        cls.school.sudo().directory_token_enc = "not-read-by-the-fake"
        cls.year = env["bf.school.year"].create({
            "name": "2026-2027", "school_id": cls.school.id,
            "date_start": date(2026, 8, 27), "date_end": date(2027, 6, 23)})
        cls.year.action_set_current()
        cls.level = env["bf.school.level"].create({
            "name": "Secondaire 1", "code": "T5SEC1", "stage": "secondary"})
        Group = env["bf.school.group"]
        cls.g301 = Group.create({"name": "301", "school_id": cls.school.id,
                                 "year_id": cls.year.id, "level_ids": [(6, 0, cls.level.ids)]})
        cls.g302 = Group.create({"name": "Les Hiboux", "school_id": cls.school.id,
                                 "year_id": cls.year.id})
        Partner = env["res.partner"]
        cls.alpha = Partner.create({"name": "Alpha Essai", "is_student": True,
                                    "student_permanent_code": "ESSA12345678"})
        cls.bravo = Partner.create({"name": "Bravo Essai", "is_student": True})
        cls.charlie = Partner.create({"name": "Charlie Essai", "is_student": True})
        Enrollment = env["bf.school.enrollment"]
        cls.e_alpha = Enrollment.create({"student_id": cls.alpha.id, "group_id": cls.g301.id})
        Enrollment.create({"student_id": cls.bravo.id, "group_id": cls.g302.id})
        cls.parent = new_test_user(env, login="school_device_parent", groups="base.group_portal")
        cls.other_parent = new_test_user(env, login="school_device_other", groups="base.group_portal")
        env["bf.school.guardian.link"].create({"student_id": cls.alpha.id,
                                               "guardian_id": cls.parent.partner_id.id})
        env["bf.school.guardian.link"].create({"student_id": cls.bravo.id,
                                               "guardian_id": cls.other_parent.partner_id.id})
        cls.office = new_test_user(env, login="school_device_office",
                                   groups="bf_school_core.group_school_manager")

        # Blue Fox OS side: the school's policy, a loan profile and a lab profile.
        company = cls.school.company_id
        env["bf.policy.org"].search([("company_id", "=", company.id)]).unlink()
        cls.org = env["bf.policy.org"].create({
            "company_id": company.id, "domain": "renard.example",
            "provision_mode": "any", **_LDAP})
        Profile = env["bf.policy.seat.profile"]
        cls.loan_profile = Profile.create({
            "name": "Prêts", "code": "pret", "org_id": cls.org.id, "kind": "loan",
            "login_groups": "it-staff", "offline_login": True})
        cls.lab_profile = Profile.create({
            "name": "Labo", "code": "labo", "org_id": cls.org.id, "kind": "lab",
            "login_groups": "students"})
        installer = env.ref("base.user_admin")
        Machine = env["bf.policy.machine"]
        cls.laptop, _t = Machine._enrol(cls.org, installer, "school-laptop-0001", "x",
                                        seat_profile=cls.loan_profile)
        cls.laptop2, _t = Machine._enrol(cls.org, installer, "school-laptop-0002", "x",
                                         seat_profile=cls.loan_profile)
        cls.lab_pc, _t = Machine._enrol(cls.org, installer, "school-lab-0001", "x",
                                        seat_profile=cls.lab_profile)

    @contextmanager
    def _directory(self):
        fake = FakeAuthentik()
        with patch.object(type(self.env["bf.school"]), "_directory_call",
                          lambda school, *a, **k: fake(school, *a, **k)):
            yield fake

    def _accounts(self):
        self.school.action_school_create_accounts()
        return self.env["bf.school.account"].search([("school_id", "=", self.school.id)])

    def _account(self, student):
        return self.env["bf.school.account"].search([("student_id", "=", student.id)])

    # ------------------------------------------------------------- accounts
    def test_one_account_per_enrolled_student(self):
        accounts = self._accounts()
        self.assertEqual(accounts.student_id, self.alpha | self.bravo,
                         "Charlie is not enrolled this year")
        self.school.action_school_create_accounts()
        self.assertEqual(len(self._accounts()), 2, "a second run creates nothing")

    def test_username_says_nothing_about_the_student(self):
        account = self._accounts().filtered(lambda a: a.student_id == self.alpha)
        self.assertRegex(account.username, r"^e\d{5}$")
        self.assertNotIn("ESSA", account.username.upper())

    def test_groups_from_level_and_class(self):
        self._accounts()
        self.assertEqual(self._account(self.alpha).directory_group_names,
                         "students students-t5sec1 students-301")
        self.assertEqual(self._account(self.bravo).directory_group_names,
                         "students students-les-hiboux")

    def test_sync_creates_user_and_groups_once(self):
        accounts = self._accounts()
        with self._directory() as fake:
            accounts.action_sync()
            alpha = self._account(self.alpha)
            user = fake.users[alpha.directory_pk]
            self.assertEqual(user["username"], alpha.username)
            self.assertEqual(user["name"], "Alpha Essai")
            self.assertTrue(user["is_active"])
            self.assertEqual(user["path"], "students")
            self.assertEqual(sorted(user["groups"]),
                             ["g-students", "g-students-301", "g-students-t5sec1"])
            self.assertFalse(accounts.filtered("sync_needed"))
            fake.calls.clear()
            self.env["bf.school.account"]._cron_sync()
            self.assertEqual(fake.calls, [], "nothing changed, nothing sent")

    def test_class_change_reaches_the_directory_by_itself(self):
        accounts = self._accounts()
        with self._directory() as fake:
            accounts.action_sync()
            alpha = self._account(self.alpha)
            # Moved to another class: no write on the account at all.
            self.e_alpha.group_id = self.g302
            self.assertTrue(alpha.sync_needed)
            self.env["bf.school.account"]._cron_sync()
            self.assertEqual(sorted(fake.users[alpha.directory_pk]["groups"]),
                             ["g-students", "g-students-les-hiboux"])

    def test_student_who_leaves_is_suspended(self):
        accounts = self._accounts()
        with self._directory() as fake:
            accounts.action_sync()
            alpha = self._account(self.alpha)
            self.e_alpha.action_leave()
            self.assertEqual(alpha.state, "suspended")
            self.env["bf.school.account"]._cron_sync()
            self.assertFalse(fake.users[alpha.directory_pk]["is_active"])

    def test_suspended_account_has_no_group(self):
        accounts = self._accounts()
        with self._directory() as fake:
            accounts.action_sync()
            alpha = self._account(self.alpha)
            self.e_alpha.action_leave()
            self.assertEqual(alpha.directory_group_names, "")
            self.env["bf.school.account"]._cron_sync()
            self.assertEqual(fake.users[alpha.directory_pk]["groups"], [])

    def test_student_who_leaves_is_dropped_from_the_lent_computer(self):
        self._accounts()
        self._lend()
        self.e_alpha.action_leave()
        with self._directory():
            self.env["bf.school.account"]._cron_sync()
        self.assertFalse(self.laptop.seat_allowed_users,
                         "the loan stays open, the login right does not")

    def test_existing_directory_user_is_adopted(self):
        accounts = self._accounts()
        alpha = self._account(self.alpha)
        with self._directory() as fake:
            fake.users[7] = {"username": alpha.username, "path": "students"}
            accounts.action_sync()
            self.assertEqual(alpha.directory_pk, 7, "no duplicate on a replay")

    def test_a_directory_user_outside_the_student_folder_is_left_alone(self):
        accounts = self._accounts()
        alpha = self._account(self.alpha)
        with self._directory() as fake, mute_logger(
                "odoo.addons.bf_school_device.models.account"):
            fake.users[8] = {"username": alpha.username, "path": "staff", "groups": ["g-admins"]}
            accounts.action_sync()
            self.assertFalse(alpha.directory_pk)
            self.assertIn("outside the students folder", alpha.sync_error)
            self.assertEqual(fake.users[8]["groups"], ["g-admins"], "not touched")

    def test_one_failure_does_not_stop_the_others(self):
        accounts = self._accounts()
        alpha, bravo = self._account(self.alpha), self._account(self.bravo)
        with self._directory() as fake, mute_logger(
                "odoo.addons.bf_school_device.models.account"):
            fake.fail_for.add(alpha.username)
            accounts.action_sync()
        self.assertIn("boom", alpha.sync_error)
        self.assertTrue(alpha.sync_needed)
        self.assertFalse(bravo.sync_error)
        self.assertFalse(bravo.sync_needed)

    def test_no_directory_says_so(self):
        accounts = self._accounts()
        self.school.directory_url = False
        accounts.action_sync()
        self.assertIn("not configured", self._account(self.alpha).sync_error)

    # ------------------------------------------------------------ passwords
    def test_password_set_in_directory_and_shown_once(self):
        accounts = self._accounts()
        alpha = self._account(self.alpha).with_user(self.office)
        with self._directory() as fake:
            accounts.action_sync()
            action = alpha.action_new_password()
        wizard = self.env["bf.school.account.password"].browse(action["res_id"])
        password = wizard.with_user(self.office).read(["password"])[0]["password"]
        self.assertEqual(fake.passwords[alpha.directory_pk], password)
        self.assertRegex(password, r"^[a-z]+-[a-z]+-\d{4}$")
        self.assertNotIn(password, str(alpha.read()), "never kept on the account")
        # Another member of the office cannot read it.
        colleague = new_test_user(self.env, login="school_device_office2",
                                  groups="bf_school_core.group_school_manager")
        with self.assertRaises(AccessError):
            wizard.with_user(colleague).read(["password"])
        # After five minutes, even its author no longer gets it.
        self.env.cr.execute(
            "UPDATE bf_school_account_password SET create_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(minutes=6), wizard.id))
        wizard.invalidate_recordset()
        self.assertFalse(wizard.with_user(self.office).read(["password"])[0]["password"])

    def test_password_from_an_office_without_email(self):
        # The office account of a real school often has no email address. The
        # password must still be shown, and the directory changed only once Odoo
        # has written everything it needs.
        self.office.partner_id.email = False
        accounts = self._accounts()
        alpha = self._account(self.alpha).with_user(self.office)
        with self._directory() as fake:
            accounts.action_sync()
            action = alpha.action_new_password()
        self.assertTrue(action["res_id"])
        self.assertIn(alpha.directory_pk, fake.passwords)

    def test_directory_untouched_when_odoo_fails_first(self):
        accounts = self._accounts()
        alpha = self._account(self.alpha)
        with self._directory() as fake, patch.object(
                type(alpha), "_message_log", side_effect=UserError("odoo first")):
            accounts.action_sync()
            with self.assertRaises(UserError):
                alpha.action_new_password()
        self.assertFalse(fake.passwords, "nothing set in the directory")

    def test_no_password_for_a_suspended_account(self):
        self._accounts()
        self.e_alpha.action_leave()
        with self.assertRaises(UserError):
            self._account(self.alpha).action_new_password()

    def test_kid_password_shape(self):
        passwords = {kid_password() for _i in range(50)}
        self.assertGreater(len(passwords), 45)
        for password in passwords:
            self.assertRegex(password, r"^[a-z]+-[a-z]+-\d{4}$")

    def test_slug(self):
        self.assertEqual(directory_slug("Les Hiboux, 3e année"), "les-hiboux-3e-annee")
        self.assertEqual(directory_slug("Élèves"), "eleves")

    # --------------------------------------------------------------- loans
    def _lend(self, student=None, machine=None, user=None):
        Loan = self.env["bf.school.device.loan"]
        if user:
            Loan = Loan.with_user(user)
        return Loan.create({"school_id": self.school.id,
                            "machine_id": (machine or self.laptop).id,
                            "student_id": (student or self.alpha).id})

    def test_loan_opens_the_machine_to_the_borrower(self):
        self._accounts()
        loan = self._lend()
        username = self._account(self.alpha).username
        self.assertEqual(self.laptop.seat_allowed_users, username)
        payload = self.org.get_policy_json(
            self.env["res.users"], seat_profile=self.loan_profile, machine=self.laptop)
        self.assertEqual(payload["install"]["login"]["allow_users"], [username])
        self.assertEqual(loan.date_due, date(2027, 6, 23), "end of the school year")
        loan.action_return()
        self.assertEqual(loan.state, "returned")
        self.assertFalse(self.laptop.seat_allowed_users, "back on the shelf")

    def test_lost_closes_the_machine(self):
        self._accounts()
        loan = self._lend()
        loan.action_mark_lost()
        self.assertFalse(self.laptop.seat_allowed_users)

    def test_no_loan_without_an_active_account(self):
        with self.assertRaises(UserError):
            self._lend()
        self._accounts()
        self.e_alpha.action_leave()
        with self.assertRaises(UserError):
            self._lend()

    def test_one_open_loan_per_computer(self):
        self._accounts()
        self._lend()
        with self.assertRaisesRegex(UserError, "already lent to Alpha Essai"):
            self._lend(student=self.bravo)
        # Two tabs at once get past the check: the index still refuses.
        Loan = self.env["bf.school.device.loan"]
        with patch.object(type(Loan), "search", lambda *a, **k: Loan.browse()), \
                mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError), \
                self.env.cr.savepoint():
            self._lend(student=self.bravo)
            self.env.flush_all()

    def test_only_loan_computers(self):
        self._accounts()
        with self.assertRaises(ValidationError):
            self._lend(machine=self.lab_pc)

    def test_change_computer_moves_the_borrower(self):
        self._accounts()
        loan = self._lend()
        loan.machine_id = self.laptop2
        self.assertFalse(self.laptop.seat_allowed_users)
        self.assertEqual(self.laptop2.seat_allowed_users, self._account(self.alpha).username)

    def test_closed_loan_is_frozen_and_open_loan_not_deleted(self):
        self._accounts()
        loan = self._lend()
        with self.assertRaises(UserError):
            loan.unlink()
        loan.action_return()
        with self.assertRaises(UserError):
            loan.student_id = self.bravo

    def test_overdue_filter(self):
        self._accounts()
        loan = self._lend()
        today = fields.Date.context_today(loan)
        loan.write({"date_out": today - timedelta(days=10),
                    "date_due": today - timedelta(days=9)})
        Loan = self.env["bf.school.device.loan"]
        self.assertIn(loan, Loan.search([("is_overdue", "=", True)]))
        loan.action_return()
        self.assertNotIn(loan, Loan.search([("is_overdue", "=", True)]))

    # -------------------------------------------------------------- notices
    def _mails(self, partner):
        return self.env["mail.mail"].sudo().search([("recipient_ids", "in", partner.ids)])

    def test_family_told_of_the_loan(self):
        self._accounts()
        self.parent.partner_id.write({"email": "parent@example.com", "lang": "en_US"})
        self.other_parent.partner_id.email = "other@example.com"
        loan = self._lend()
        mails = self._mails(self.parent.partner_id)
        self.assertEqual(len(mails), 1)
        self.assertIn("a computer lent by the school", mails.subject)
        self.assertIn(self.laptop.hostname, mails.body_html)
        self.assertFalse(self._mails(self.other_parent.partner_id), "another child's family")
        self.assertTrue(loan.notice_out_on)

    def test_a_notice_goes_once_whoever_calls_it(self):
        self._accounts()
        self.parent.partner_id.email = "parent@example.com"
        loan = self._lend()
        loan._notify_families("bf_school_device.mail_template_loan_out", "notice_out_on")
        self.assertEqual(len(self._mails(self.parent.partner_id)), 1)

    def test_only_adults_who_receive_notices(self):
        self._accounts()
        self.parent.partner_id.email = "parent@example.com"
        self.env["bf.school.guardian.link"].search(
            [("guardian_id", "=", self.parent.partner_id.id)]).receives_notices = False
        loan = self._lend()
        self.assertFalse(self._mails(self.parent.partner_id))
        self.assertTrue(loan.notice_out_on, "stamped anyway: nobody retried forever")

    def test_reminder_seven_days_before_then_overdue_once(self):
        self._accounts()
        self.parent.partner_id.email = "parent@example.com"
        loan = self._lend()
        today = fields.Date.context_today(loan)
        Loan = self.env["bf.school.device.loan"]
        loan.date_due = today + timedelta(days=8)
        Loan._cron_loan_notices()
        self.assertFalse(loan.notice_reminder_on, "not yet: 8 days left")
        loan.date_due = today + timedelta(days=7)
        Loan._cron_loan_notices()
        Loan._cron_loan_notices()
        self.assertTrue(loan.notice_reminder_on)
        self.assertEqual(len(self._mails(self.parent.partner_id).filtered(
            lambda m: "is due back on" in m.subject)), 1, "once")
        loan.write({"date_out": today - timedelta(days=20), "date_due": today - timedelta(days=1)})
        Loan._cron_loan_notices()
        Loan._cron_loan_notices()
        overdue = self._mails(self.parent.partner_id).filtered(
            lambda m: "has not come back" in m.subject)
        self.assertEqual(len(overdue), 1, "once")

    def test_no_notice_for_a_returned_computer(self):
        self._accounts()
        self.parent.partner_id.email = "parent@example.com"
        loan = self._lend()
        loan.action_return()
        loan.write({"date_out": loan.date_out - timedelta(days=30),
                    "date_due": loan.date_out - timedelta(days=29)})
        self.env["bf.school.device.loan"]._cron_loan_notices()
        self.assertFalse(loan.notice_overdue_on)

    # ------------------------------------------------------ found in QA
    def test_office_cannot_redirect_the_directory(self):
        # Whoever sets the URL chooses where the decrypted token goes.
        with self.assertRaises(AccessError):
            self.school.with_user(self.office).write({"directory_url": "https://evil.example"})
        with self.assertRaises(AccessError):
            self.school.with_user(self.office).write({"directory_student_group": "it-staff"})

    def test_new_url_drops_the_old_token(self):
        self.school.directory_url = "https://other.example"
        self.assertFalse(self.school.sudo().directory_token_enc)

    def test_read_only_staff_cannot_reach_the_directory(self):
        accounts = self._accounts()
        staff = new_test_user(self.env, login="school_device_staff",
                              groups="bf_school_core.group_school_user")
        with self._directory() as fake, self.assertRaises(AccessError):
            accounts.with_user(staff).action_sync()
        self.assertEqual(fake.calls, [])

    def test_a_loan_keeps_its_student(self):
        self._accounts()
        loan = self._lend()
        with self.assertRaises(UserError):
            loan.student_id = self.bravo

    def test_closing_a_loan_by_any_path_closes_the_machine(self):
        self._accounts()
        loan = self._lend()
        loan.write({"state": "returned"})  # an import, an RPC: not the button
        self.assertFalse(self.laptop.seat_allowed_users)

    def test_busy_computer_of_another_company_names_nobody(self):
        self._accounts()
        loan = self._lend()
        other = self.env["res.company"].create({"name": "Autre école"})
        self.office.write({"company_ids": [(4, other.id)], "company_id": other.id})
        Loan = self.env["bf.school.device.loan"].with_user(self.office).with_context(
            allowed_company_ids=[other.id])
        with self.assertRaises(UserError) as caught:
            Loan.create({"school_id": self.school.id, "machine_id": self.laptop.id,
                         "student_id": self.bravo.id})
        self.assertNotIn("Alpha", str(caught.exception))

    def test_expired_password_hidden_everywhere_and_purged(self):
        accounts = self._accounts()
        with self._directory():
            accounts.action_sync()
            action = self._account(self.alpha).with_user(self.office).action_new_password()
        Wizard = self.env["bf.school.account.password"]
        wizard = Wizard.browse(action["res_id"])
        self.env.cr.execute(
            "UPDATE bf_school_account_password SET create_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(minutes=6), wizard.id))
        wizard.invalidate_recordset()
        rows = Wizard.with_user(self.office).search_read([("id", "=", wizard.id)], ["password"])
        self.assertFalse(rows[0]["password"], "search_read too")
        with self._directory():
            self.env["bf.school.account"]._cron_sync()
        self.assertFalse(wizard.exists(), "gone from the database")

    def test_done_deletes_the_password(self):
        accounts = self._accounts()
        with self._directory():
            accounts.action_sync()
            action = self._account(self.alpha).with_user(self.office).action_new_password()
        wizard = self.env["bf.school.account.password"].browse(action["res_id"])
        wizard.with_user(self.office).action_done()
        self.assertFalse(wizard.exists())

    def test_office_cannot_write_system_fields_of_an_account(self):
        # directory_pk pointed at an Authentik administrator would have had "New
        # password" set that administrator's password.
        self._accounts()
        alpha = self._account(self.alpha).with_user(self.office)
        for vals in ({"directory_pk": 1}, {"username": "akadmin"},
                     {"password_set_on": fields.Datetime.now()}, {"student_id": self.bravo.id}):
            with self.assertRaises(AccessError, msg=vals):
                alpha.write(vals)

    def test_office_cannot_write_a_loan_status_or_its_notices(self):
        self._accounts()
        loan = self._lend(user=self.office)
        for vals in ({"state": "returned"}, {"date_returned": loan.date_out},
                     {"notice_reminder_on": fields.Datetime.now()}):
            with self.assertRaises(AccessError, msg=vals):
                loan.write(vals)
        loan.action_return()
        self.assertEqual(loan.state, "returned", "the button still works for the office")

    def test_a_closed_loan_is_a_record(self):
        self._accounts()
        loan = self._lend(user=self.office)
        loan.action_return()
        with self.assertRaises(UserError):
            loan.write({"date_due": loan.date_out})
        loan.write({"condition_in": "Écran rayé"})
        self.assertEqual(loan.condition_in, "Écran rayé")

    def test_read_only_staff_cannot_close_a_loan(self):
        self._accounts()
        loan = self._lend()
        staff = new_test_user(self.env, login="school_device_reader",
                              groups="bf_school_core.group_school_user")
        with self.assertRaises(AccessError):
            loan.with_user(staff).action_return()
        self.assertEqual(loan.state, "out")

    # --------------------------------------------------------------- rights
    def test_office_lends_and_sees_only_loan_computers(self):
        self._accounts()
        loan = self._lend(user=self.office)
        self.assertEqual(loan.machine_id, self.laptop)
        visible = self.env["bf.policy.machine"].with_user(self.office).search([])
        self.assertIn(self.laptop, visible)
        self.assertNotIn(self.lab_pc, visible)
        # The borrower was written although the office cannot write machines.
        self.assertTrue(self.laptop.seat_allowed_users)
        with self.assertRaises(AccessError):
            self.laptop.with_user(self.office).write({"seat_allowed_users": "e99999"})

    def test_system_admin_in_the_office_still_sees_the_fleet(self):
        admin = self.env.ref("base.user_admin")
        admin.groups_id = [(4, self.env.ref("bf_school_core.group_school_manager").id)]
        visible = self.env["bf.policy.machine"].with_user(admin).search([])
        self.assertIn(self.lab_pc, visible)

    # --------------------------------------------------------------- portal
    def test_family_sees_their_child_computer_only(self):
        self._accounts()
        self._lend()
        self._lend(student=self.bravo, machine=self.laptop2)
        self.authenticate("school_device_parent", "school_device_parent")
        page = self.url_open("/my/school").text
        self.assertIn(self.laptop.hostname, page)
        self.assertNotIn(self.laptop2.hostname, page)

    def test_machine_readable_by_a_system_admin_outside_the_school(self):
        # Adding a field on bf.policy.machine must not lock its own admins out.
        sysadmin = new_test_user(self.env, login="school_device_sysadmin", groups="base.group_system")
        self.assertTrue(self.laptop.with_user(sysadmin).read()[0]["hostname"])
