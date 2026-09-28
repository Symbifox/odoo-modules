from datetime import date
from unittest.mock import patch

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSchoolPrivacy(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.school = env["bf.school"].create({"name": "École des Essais"})
        year = env["bf.school.year"].create({
            "name": "2026-2027", "school_id": cls.school.id,
            "date_start": date(2026, 8, 27), "date_end": date(2027, 6, 23)})
        year.action_set_current()
        group = env["bf.school.group"].create({"name": "301", "school_id": cls.school.id, "year_id": year.id})
        today = fields.Date.context_today(env["res.partner"])
        Partner = env["res.partner"]
        cls.child = Partner.create({"name": "Enfant Essai", "is_student": True,
                                    "student_birthdate": today - relativedelta(years=9)})
        cls.teen = Partner.create({"name": "Ado Essai", "is_student": True,
                                   "email": "ado@example.invalid",
                                   "student_birthdate": today - relativedelta(years=15)})
        cls.left = Partner.create({"name": "Parti Essai", "is_student": True,
                                   "student_birthdate": today - relativedelta(years=9)})
        for kid in cls.child | cls.teen | cls.left:
            env["bf.school.enrollment"].create({"student_id": kid.id, "group_id": group.id})
        cls.left.student_enrollment_ids.action_leave()
        cls.mom = Partner.create({"name": "Maman Essai", "email": "priv.mom@example.invalid"})
        cls.granny = Partner.create({"name": "Mamie Essai", "email": "priv.granny@example.invalid"})
        Link = env["bf.school.guardian.link"]
        Link.create({"student_id": cls.child.id, "guardian_id": cls.mom.id})
        Link.create({"student_id": cls.child.id, "guardian_id": cls.granny.id,
                     "has_parental_authority": False, "can_sign": False})
        cls.office = new_test_user(env, login="school_priv_office",
                                   groups="bf_school_core.group_school_manager"
                                   if env.ref("privacy_consent.group_privacy_manager", raise_if_not_found=False)
                                   else "bf_school_core.group_school_manager")
        cls.teacher = new_test_user(env, login="school_priv_teacher", groups="bf_school_core.group_school_user")

    def _request(self):
        action = self.school.action_school_request_consents()
        wizard = self.env[action["res_model"]].with_context(**action["context"]).create({})
        wizard.action_create_requests()
        return self.env["privacy.consent"].search(
            [("subject_partner_id", "in", (self.child | self.teen | self.left).ids)])

    def test_one_consent_per_student_and_purpose(self):
        consents = self._request()
        self.assertEqual(len(consents), 2 * 4, "two students enrolled today, four purposes")
        self.assertNotIn(self.left, consents.subject_partner_id, "a student who left is not asked")
        self.assertEqual(set(consents.mapped("purpose_id.code")),
                         {"school_photo_internal", "school_photo_public", "school_directory", "school_edtech"})

    def test_minor_is_asked_through_parental_authority(self):
        consents = self._request().filtered(lambda c: c.subject_partner_id == self.child)
        self.assertTrue(all(consents.mapped("is_minor")))
        for consent in consents:
            self.assertEqual(consent.given_by_partner_ids, self.mom, "the grandmother does not consent")

    def test_student_of_fifteen_consents_alone(self):
        consents = self._request().filtered(lambda c: c.subject_partner_id == self.teen)
        self.assertFalse(any(consents.mapped("is_minor")))
        self.assertFalse(consents.given_by_partner_ids)

    def test_asking_twice_creates_nothing_new(self):
        first = self._request()
        action = self.school.action_school_request_consents()
        wizard = self.env[action["res_model"]].with_context(**action["context"]).create({})
        with self.assertRaises(Exception):
            wizard.action_create_requests()
        self.assertEqual(len(self.env["privacy.consent"].search(
            [("subject_partner_id", "in", (self.child | self.teen).ids)])), len(first))

    def test_guardian_is_emailed_not_the_child(self):
        """Who privacy_consent writes to, read at its sending call.

        🔴 privacy_consent sends at once and then deletes its own message, which
        deletes the `mail.mail` in cascade: without a working mail server (the bench,
        the tests) no trace is left, and the chatter still says "sent". The mail
        table cannot prove anything here; the call can.
        """
        Consent = type(self.env["privacy.consent"])
        asked = []
        original = Consent._send_single_consent_email

        def spy(consent, template, contact):
            asked.append(contact)

        with patch.object(Consent, "_send_single_consent_email", spy):
            self._request()
        emails = {c.email for c in asked}
        self.assertIn(self.mom.email, emails)
        self.assertNotIn(self.granny.email, emails, "no parental authority, not asked")
        self.assertIn(self.teen.email, emails, "the student of 15 is asked")
        self.assertTrue(original)

    def test_teacher_cannot_ask(self):
        with self.assertRaises(AccessError):
            self.school.with_user(self.teacher).action_school_request_consents()

    def test_stale_minor_flag_is_refreshed_at_request(self):
        """A student who turned 14 since the last cron consents alone on the day asked."""
        # As if the daily cron had not run since the birthday.
        self.env.cr.execute("UPDATE res_partner SET is_minor_child = TRUE WHERE id = %s", [self.teen.id])
        self.teen.invalidate_recordset(["is_minor_child"])
        self.assertTrue(self.teen.is_minor_child)
        consents = self._request().filtered(lambda c: c.subject_partner_id == self.teen)
        self.assertFalse(any(consents.mapped("is_minor")))

    # Adversarial review (2026-09-27)
    def test_nobody_to_ask_is_left_out_not_the_child_asked(self):
        today = fields.Date.context_today(self.env["res.partner"])
        group = self.child.student_enrollment_ids.group_id
        unknown = self.env["res.partner"].create({"name": "Sans Date Essai", "is_student": True,
                                                  "email": "sansdate@example.invalid"})
        alone = self.env["res.partner"].create({"name": "Seul Essai", "is_student": True,
                                                "email": "seul@example.invalid",
                                                "student_birthdate": today - relativedelta(years=8)})
        for kid in unknown | alone:
            self.env["bf.school.enrollment"].create({"student_id": kid.id, "group_id": group.id})
        action = self.school.action_school_request_consents()
        students = self.env["res.partner"].browse(action["context"]["default_partner_ids"][0][2])
        self.assertNotIn(unknown, students)
        self.assertNotIn(alone, students)
        self.assertIn(self.child, students)
        note = self.school.message_ids[:1].body
        self.assertIn("Sans Date Essai", note)
        self.assertIn("Seul Essai", note)
