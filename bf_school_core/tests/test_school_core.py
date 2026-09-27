from datetime import date
from unittest.mock import patch

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class SchoolCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.school = cls.env["bf.school"].create({"name": "École des Essais"})
        cls.year = cls.env["bf.school.year"].create({
            "name": "2026-2027", "school_id": cls.school.id,
            "date_start": date(2026, 8, 27), "date_end": date(2027, 6, 23)})
        cls.year.action_set_current()
        cls.group = cls.env["bf.school.group"].create({
            "name": "301", "school_id": cls.school.id, "year_id": cls.year.id,
            "level_ids": [(6, 0, [cls.env.ref("bf_school_core.level_e3").id])]})
        Partner = cls.env["res.partner"]
        cls.mom = Partner.create({"name": "Parent A", "email": "a@example.invalid"})
        cls.dad = Partner.create({"name": "Parent B", "email": "b@example.invalid"})
        cls.granny = Partner.create({"name": "Grand-parent C"})
        cls.today = fields.Date.context_today(Partner)
        cls.child = Partner.create({
            "name": "Élève Un", "is_student": True,
            "student_birthdate": cls.today - relativedelta(years=9)})


@tagged("post_install", "-at_install")
class TestMinor(SchoolCase):

    def test_minor_from_birthdate(self):
        self.assertTrue(self.child.is_minor_child)
        self.assertEqual(self.child.student_age, 9)

    def test_fourteenth_birthday_switches_by_cron(self):
        teen = self.env["res.partner"].create({
            "name": "Élève Ado", "is_student": True,
            "student_birthdate": self.today - relativedelta(years=14, days=-1)})
        self.assertTrue(teen.is_minor_child, "13 years and 364 days is still a minor")
        tomorrow = self.today + relativedelta(days=1)
        with patch.object(fields.Date, "context_today", return_value=tomorrow):
            self.env["res.partner"]._cron_school_sync_minors()
        self.assertFalse(teen.is_minor_child, "the birthday flips it without anyone acting")

    def test_threshold_follows_framework(self):
        company = self.env.company
        if "default_privacy_framework_id" not in company._fields:
            self.skipTest("privacy framework not on company")
        framework = company.default_privacy_framework_id
        if not framework:
            self.skipTest("no default framework")
        framework.age_of_majority = 16
        fifteen = self.env["res.partner"].create({
            "name": "Élève Quinze", "is_student": True,
            "student_birthdate": self.today - relativedelta(years=15)})
        self.assertTrue(fifteen.is_minor_child)

    def test_no_birthdate_changes_nothing(self):
        partner = self.env["res.partner"].create({
            "name": "Élève Sans Date", "is_student": True, "is_minor_child": True})
        self.assertTrue(partner.is_minor_child)


@tagged("post_install", "-at_install")
class TestGuardians(SchoolCase):

    def _link(self, guardian, **roles):
        return self.env["bf.school.guardian.link"].create(
            dict({"student_id": self.child.id, "guardian_id": guardian.id}, **roles))

    def test_parental_authority_mirrored(self):
        self._link(self.mom)
        self._link(self.dad)
        self._link(self.granny, has_parental_authority=False, can_sign=False)
        self.assertEqual(self.child.legal_guardian_ids, self.mom | self.dad)

    def test_losing_authority_unmirrors(self):
        link = self._link(self.dad)
        self._link(self.mom)
        link.write({"has_parental_authority": False, "can_sign": False})
        self.assertEqual(self.child.legal_guardian_ids, self.mom)
        self._link(self.granny, has_parental_authority=False, can_sign=False).unlink()
        link.unlink()
        self.assertEqual(self.child.legal_guardian_ids, self.mom)

    def test_signer_needs_authority(self):
        with self.assertRaises(ValidationError), mute_logger("odoo.sql_db"):
            self._link(self.granny, has_parental_authority=False, can_sign=True)

    def test_one_link_per_adult(self):
        self._link(self.mom)
        with self.assertRaises(Exception), mute_logger("odoo.sql_db"):
            with self.env.cr.savepoint():
                self._link(self.mom)

    def test_children_for_guardian_by_role(self):
        self._link(self.mom, is_payer=True)
        self._link(self.dad)
        self.assertEqual(self.mom._school_children_for_guardian("is_payer"), self.child)
        self.assertFalse(self.dad._school_children_for_guardian("is_payer"))
        self.assertEqual(self.dad._school_children_for_guardian(), self.child)


@tagged("post_install", "-at_install")
class TestStudent(SchoolCase):

    def test_permanent_code_normalised(self):
        self.child.student_permanent_code = " abcd 1234 5678 "
        self.assertEqual(self.child.student_permanent_code, "ABCD12345678")

    def test_permanent_code_format(self):
        with self.assertRaises(ValidationError):
            self.child.student_permanent_code = "ABC123"

    def test_enrollment_and_current_groups(self):
        enrollment = self.env["bf.school.enrollment"].create(
            {"student_id": self.child.id, "group_id": self.group.id})
        self.assertEqual(self.child.student_group_ids, self.group)
        self.assertEqual(self.school.student_count, 1)
        enrollment.action_leave()
        self.assertFalse(self.child.student_group_ids)
        self.assertEqual(enrollment.date_end, self.today)

    def test_enrollment_requires_student(self):
        with self.assertRaises(ValidationError):
            self.env["bf.school.enrollment"].create(
                {"student_id": self.mom.id, "group_id": self.group.id})

    def test_one_current_year(self):
        other = self.env["bf.school.year"].create({
            "name": "2027-2028", "school_id": self.school.id,
            "date_start": date(2027, 8, 26), "date_end": date(2028, 6, 22)})
        with self.assertRaises(ValidationError):
            other.state = "current"
        other.action_set_current()
        self.assertEqual(self.year.state, "closed")
        self.assertEqual(self.school.current_year_id, other)


@tagged("post_install", "-at_install")
class TestAccess(SchoolCase):

    def test_staff_reads_but_does_not_write_links(self):
        teacher = new_test_user(self.env, login="school_teacher",
                                groups="bf_school_core.group_school_user")
        link = self.env["bf.school.guardian.link"].create(
            {"student_id": self.child.id, "guardian_id": self.mom.id})
        self.assertEqual(link.with_user(teacher).guardian_id, self.mom)
        with self.assertRaises(AccessError):
            link.with_user(teacher).write({"is_payer": True})

    def test_portal_user_reads_nothing(self):
        parent = new_test_user(self.env, login="school_portal", groups="base.group_portal")
        with self.assertRaises(AccessError):
            self.env["bf.school.guardian.link"].with_user(parent).search([])

    def test_office_enters_a_birth_date(self):
        """The school office, without any privacy right, creates a student with a birth date."""
        office = new_test_user(self.env, login="school_office_only",
                               groups="bf_school_core.group_school_manager")
        student = self.env["res.partner"].with_user(office).create({
            "name": "Élève Secrétariat", "is_student": True,
            "student_birthdate": self.today - relativedelta(years=7)})
        self.assertTrue(student.is_minor_child)


@tagged("post_install", "-at_install")
class TestSchoolMail(TransactionCase):
    """Every school email leaves in the tenant's layout, its links in the recipient's language."""

    def _send(self, **ctx):
        template = self.env["mail.template"].create({
            "name": "Essai lien", "model_id": self.env.ref("base.model_res_partner").id,
            "subject": "Essai", "email_to": "essai@example.invalid",
            "body_html": '<p><a t-attf-href="{{ object.get_base_url() }}/my/school">portail</a> '
                         '<a t-attf-href="{{ object.get_base_url() }}/web/login">connexion</a></p>'})
        partner = self.env["res.partner"].create({"name": "Parent Lien", "email": "lien@example.invalid"})
        mail_id = template.with_context(**ctx).send_mail(partner.id)
        return self.env["mail.mail"].browse(mail_id), partner.get_base_url().rstrip("/")

    def setUp(self):
        super().setUp()
        # As on a tenant with the website: the prefix exists for fr_CA.
        patcher = patch.object(type(self.env["bf.school"]), "_school_url_lang",
                               lambda self, lang: "/fr" if lang == "fr_CA" else "")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_school_links_carry_the_language(self):
        mail, base = self._send(lang="fr_CA", school_lang="fr_CA")
        self.assertIn('href="%s/fr/my/school"' % base, mail.body_html)
        self.assertIn('href="%s/web/login"' % base, mail.body_html, "/web routes take no prefix")

    def test_other_emails_are_untouched(self):
        mail, base = self._send(lang="fr_CA")
        self.assertIn('href="%s/my/school"' % base, mail.body_html)
