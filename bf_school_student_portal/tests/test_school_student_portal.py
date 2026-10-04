import html
import json
import re
from datetime import date
from urllib.parse import parse_qs, unquote_plus, urlparse

from odoo.exceptions import AccessDenied, AccessError, UserError, ValidationError
from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSchoolStudentPortal(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.provider = env["auth.oauth.provider"].create({
            "name": "Annuaire de l'école", "client_id": "ecole-portail", "enabled": True,
            "body": "Élèves",
            "auth_endpoint": "https://auth.ecole.test/application/o/authorize/",
            "validation_endpoint": "https://auth.ecole.test/application/o/userinfo/",
            "scope": "openid profile"})
        cls.school = env["bf.school"].create({"name": "École des Essais", "code": "essais",
                                              "directory_url": "https://auth.ecole.test"})
        cls.school.student_oauth_provider_id = cls.provider
        year = env["bf.school.year"].create({"name": "2026-2027", "school_id": cls.school.id,
                                             "date_start": date(2026, 8, 27), "date_end": date(2027, 6, 23)})
        year.action_set_current()
        cls.group = env["bf.school.group"].create({"name": "301", "school_id": cls.school.id,
                                                   "year_id": year.id})
        cls.student = env["res.partner"].create({"name": "Alpha Essai", "is_student": True})
        cls.enrollment = env["bf.school.enrollment"].create(
            {"student_id": cls.student.id, "group_id": cls.group.id})
        cls.account = env["bf.school.account"].create(
            {"student_id": cls.student.id, "school_id": cls.school.id})
        cls.account.sudo().directory_pk = 4242
        cls.office = new_test_user(env, login="school_sp_office",
                                   groups="bf_school_core.group_school_manager")

    def _signin(self, subject):
        return self.env["res.users"].sudo()._auth_oauth_signin(
            self.provider.id, {"user_id": subject}, {"access_token": "jeton", "state": "{}"})

    def test_portal_user_born_from_the_account(self):
        self.account._school_portal_sync()
        user = self.account.portal_user_id
        self.assertTrue(user.share)
        self.assertEqual(user.partner_id, self.student)
        self.assertEqual((user.oauth_provider_id, user.oauth_uid), (self.provider, "4242"))
        self.env.flush_all()
        self.env.cr.execute("SELECT password FROM res_users WHERE id = %s", [user.id])
        self.assertFalse(self.env.cr.fetchone()[0], "no password of its own")
        self.assertEqual(self._signin("4242"), user.login)

    def test_sign_in_never_creates_a_user(self):
        self.env["ir.config_parameter"].sudo().set_param("auth_signup.invitation_scope", "b2c")
        before = self.env["res.users"].with_context(active_test=False).search_count([])
        self.assertFalse(self._signin("9999"))
        self.assertEqual(self.env["res.users"].with_context(active_test=False).search_count([]), before)

    def test_follows_the_enrolment(self):
        self.account._school_portal_sync()
        user = self.account.portal_user_id
        self.enrollment.state = "left"
        self.assertEqual(self.account.state, "suspended")
        self.account.action_sync()  # the directory is unreachable: the user is archived anyway
        self.assertFalse(user.active)
        self.assertTrue(self.student.active, "the student's card stays")
        self.assertFalse(self._signin("4242"))
        self.enrollment.state = "active"
        self.account._school_portal_sync()
        self.assertTrue(user.active)
        self.assertEqual(self._signin("4242"), user.login)

    def test_provider_of_another_directory_refused(self):
        other = self.provider.copy({
            "name": "Autre annuaire",
            "auth_endpoint": "https://auth.ailleurs.test/application/o/authorize/",
            "validation_endpoint": "https://auth.ailleurs.test/application/o/userinfo/"})
        with self.assertRaises(ValidationError):
            self.school.student_oauth_provider_id = other

    def test_office_cannot_point_the_portal_user(self):
        staff = new_test_user(self.env, login="school_sp_staff", groups="base.group_user")
        with self.assertRaises(AccessError):
            self.account.with_user(self.office).write({"portal_user_id": staff.id})

    def test_student_does_not_edit_nor_delete(self):
        self.account._school_portal_sync()
        user = self.account.portal_user_id
        user.sudo().password = "school_sp_alpha"
        with self.assertRaises(UserError):
            user.sudo()._deactivate_portal_user()
        # The pages, not the door: through the directory, a password does not sign a student in
        # (test_password_refused_through_the_directory), so this school goes without one here.
        self.school.student_oauth_provider_id = False
        self.authenticate(user.login, "school_sp_alpha")
        # (the website first adds its language prefix, then the module sends the student away)
        self.assertTrue(self.url_open("/my/account").url.endswith("/my/school"))
        page = self.url_open("/my/school").text
        self.assertIn("Alpha Essai", page)
        self.assertNotIn("has not linked any child", page)
        self.assertNotIn('href="/my/account"', self.url_open("/my/home").text)

    # ---------------------------------------------------------------- review, 2026-10-03

    def test_office_cannot_choose_the_portal_user(self):
        bravo = self.env["res.partner"].create({"name": "Bravo Essai", "is_student": True})
        admin = self.env.ref("base.user_admin")
        with self.assertRaises(AccessError):
            self.env["bf.school.account"].with_user(self.office).create(
                {"student_id": bravo.id, "school_id": self.school.id, "portal_user_id": admin.id})
        # Even if one were set by other means, the synchronisation only writes a student's user.
        account = self.env["bf.school.account"].create({"student_id": bravo.id, "school_id": self.school.id})
        account.sudo().write({"directory_pk": 4243, "portal_user_id": admin.id})
        account._school_portal_sync()
        self.assertFalse(admin.oauth_uid)
        self.assertTrue(admin.active)

    def test_office_synchronises(self):
        self.account.with_user(self.office).action_sync()
        self.assertTrue(self.account.portal_user_id, "the office's button creates the user")

    def test_a_guardian_is_not_made_a_student_user(self):
        parent = self.env["res.partner"].create({"name": "Parent Essai", "is_student": True})
        self.env["bf.school.guardian.link"].create({"student_id": self.student.id, "guardian_id": parent.id})
        self.env["bf.school.enrollment"].create({"student_id": parent.id, "group_id": self.group.id})
        account = self.env["bf.school.account"].create({"student_id": parent.id, "school_id": self.school.id})
        account.sudo().directory_pk = 4244
        (account | self.account)._school_portal_sync()
        self.assertFalse(account.portal_user_id)
        self.assertTrue(self.account.portal_user_id, "one refused account does not stop the others")

    def test_student_keeps_the_card_and_the_directory(self):
        self.account._school_portal_sync()
        user = self.account.portal_user_id
        self.student.email = "parent.essai@example.com"
        with self.assertRaises(AccessError):
            user.with_user(user).write({"name": "Tom le Pirate"})
        user.with_user(user).write({"lang": "fr_CA"})
        self.assertEqual(self.student.name, "Alpha Essai")
        with self.assertRaises(UserError):
            user.sudo().action_reset_password()

    def test_child_under_14_does_not_consent(self):
        from odoo.addons.bf_school_student_portal.controllers.portal import SchoolStudentPrivacy
        purpose = self.env["privacy.purpose"].create(
            {"code": "ESSAI_ECOLE", "name": "Essai", "default_validity_days": 365})
        consent = self.env["privacy.consent"].create(
            {"subject_partner_id": self.student.id, "purpose_id": purpose.id, "status": "pending"})
        controller = SchoolStudentPrivacy()
        self.student.is_minor_child = True
        self.assertFalse(controller._can_access_consent(self.student, consent))
        self.student.is_minor_child = False  # 14 or more: the student is the one who consents
        self.assertTrue(controller._can_access_consent(self.student, consent))

    # ---------------------------------------------------------------- review, 2026-10-04

    def test_password_refused_through_the_directory(self):
        """The directory is the only door: a password an administrator gave does not open it."""
        self.account._school_portal_sync()
        user = self.account.portal_user_id
        user.sudo().password = "school_sp_alpha"
        credential = {"type": "password", "password": "school_sp_alpha"}
        with self.assertRaises(AccessDenied):
            user.with_user(user)._check_credentials(credential, {"interactive": True})
        self.school.student_oauth_provider_id = False
        user.with_user(user)._check_credentials(credential, {"interactive": True})

    def test_provider_moved_to_another_directory_refused(self):
        with self.assertRaises(ValidationError):
            self.provider.auth_endpoint = "https://auth.ailleurs.test/application/o/authorize/"

    def test_student_does_not_read_the_school_fields_of_their_card(self):
        """Permanent code, birth date, health alert and allergies: the staff's, read in sudo by the
        portal pages, not by the student over RPC."""
        self.student.write({"student_permanent_code": "ESSA12345678", "student_birthdate": date(2014, 5, 4),
                            "comment": "<p>Garde partagée, voir le dossier</p>"})
        self.account._school_portal_sync()
        card = self.student.with_user(self.account.portal_user_id)
        names = [f for f in ("comment", "student_permanent_code", "student_birthdate", "student_age",
                             "health_alert", "health_alert_level", "meal_allergen_ids")
                 if f in self.student._fields]
        for name in names:
            with self.assertRaises(AccessError, msg=name):
                card.read([name])
        self.assertEqual(card.read(["name"])[0]["name"], "Alpha Essai")

    def test_directory_moved_away_from_the_provider_refused(self):
        with self.assertRaises(ValidationError):
            self.school.directory_url = "https://auth.ailleurs.test"
        self.provider.enabled = False  # not an address: disabling stays possible

    # ---------------------------------------------------------------- landing, 2026-10-04

    def _returns(self, url):
        """{provider id: where it returns after sign-in}, read from the sign-in page's links."""
        page = self.url_open(url).text
        returns = {}
        for link in re.findall(r'href="([^"]*state=[^"]*)"', page):
            state = json.loads(parse_qs(urlparse(html.unescape(link)).query)["state"][0])
            returns[state["p"]] = unquote_plus(state["r"])
        return returns

    def test_student_lands_on_their_portal(self):
        """Through the school's directory, the student lands on their portal, not on the
        website's home page (auth_oauth returns to /web, then sends a portal user to /)."""
        other = self.env["auth.oauth.provider"].create({
            "name": "Autre fournisseur", "client_id": "autre", "enabled": True, "body": "Autre",
            "auth_endpoint": "https://ailleurs.test/authorize",
            "validation_endpoint": "https://ailleurs.test/userinfo", "scope": "openid"})
        returns = self._returns("/web/login")
        self.assertTrue(returns[self.provider.id].endswith("/my/school"), returns)
        self.assertTrue(returns[other.id].endswith("/web"), "another provider is left alone")
        returns = self._returns("/web/login?redirect=/my/school/work")
        self.assertTrue(returns[self.provider.id].endswith("/my/school/work"), "an explicit redirect is kept")

