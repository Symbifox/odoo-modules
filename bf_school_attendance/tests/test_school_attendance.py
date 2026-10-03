import re
from datetime import date, timedelta

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class AttendanceCase(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.school = env["bf.school"].create({"name": "École des Essais"})
        year = env["bf.school.year"].create({"name": "2026-2027", "school_id": cls.school.id,
                                             "date_start": date(2026, 8, 27), "date_end": date(2027, 6, 23)})
        year.action_set_current()
        cls.t1 = new_test_user(env, login="school_att_t1", groups="bf_school_core.group_school_user")
        cls.t2 = new_test_user(env, login="school_att_t2", groups="bf_school_core.group_school_user")
        Group = env["bf.school.group"]
        cls.g301 = Group.create({"name": "301", "school_id": cls.school.id, "year_id": year.id,
                                 "teacher_ids": [(6, 0, cls.t1.ids)]})
        cls.g5 = Group.create({"name": "Sec 5", "school_id": cls.school.id, "year_id": year.id,
                               "teacher_ids": [(6, 0, cls.t2.ids)], "attendance_mode": "period",
                               "periods_per_day": 4})
        Partner = env["res.partner"]
        cls.a = Partner.create({"name": "Alpha Essai", "is_student": True})
        cls.b = Partner.create({"name": "Bravo Essai", "is_student": True})
        cls.c = Partner.create({"name": "Charlie Essai", "is_student": True})
        Enrollment = env["bf.school.enrollment"]
        for child, group in ((cls.a, cls.g301), (cls.b, cls.g301), (cls.c, cls.g5)):
            Enrollment.create({"student_id": child.id, "group_id": group.id})
        cls.p = new_test_user(env, login="school_att_p", name="Parent P", email="att.p@example.invalid",
                              groups="base.group_portal")
        cls.q = new_test_user(env, login="school_att_q", name="Parent Q", email="att.q@example.invalid",
                              groups="base.group_portal")
        cls.quiet = Partner.create({"name": "Parent Discret", "email": "att.quiet@example.invalid"})
        Link = env["bf.school.guardian.link"]
        Link.create({"student_id": cls.a.id, "guardian_id": cls.p.partner_id.id})
        Link.create({"student_id": cls.a.id, "guardian_id": cls.quiet.id, "receives_notices": False})
        Link.create({"student_id": cls.b.id, "guardian_id": cls.q.partner_id.id})
        cls.today = fields.Date.context_today(env["res.partner"])
        cls.illness = env.ref("bf_school_attendance.reason_illness")

    def _session(self, group=None, slot="am", day=None, user=None):
        Session = self.env["bf.school.attendance.session"]
        if user:
            Session = Session.with_user(user)
        return Session.create({"group_id": (group or self.g301).id, "slot": slot, "date": day or self.today})

    def _line(self, session, student):
        return session.line_ids.filtered(lambda l: l.student_id == student)

    def _mails_to(self, partner):
        return self.env["mail.mail"].sudo().search([("recipient_ids", "in", partner.ids)])

    def _csrf(self, url):
        page = self.url_open(url).text
        match = re.search(r'name="csrf_token" value="([^"]+)"', page)
        self.assertTrue(match)
        return match.group(1)


@tagged("post_install", "-at_install")
class TestRollCall(AttendanceCase):

    def test_everyone_present_by_default(self):
        session = self._session()
        self.assertEqual(session.line_ids.student_id, self.a | self.b)
        self.assertEqual(set(session.line_ids.mapped("status")), {"present"})

    def test_declared_absence_prefilled_and_justified(self):
        self.env["bf.school.absence.declaration"].create({
            "student_id": self.a.id, "date_from": self.today, "date_to": self.today,
            "part": "am", "reason_id": self.illness.id})
        am, pm = self._session(slot="am"), self._session(slot="pm")
        self.assertEqual((self._line(am, self.a).status, self._line(am, self.a).justified), ("absent", True))
        self.assertEqual(self._line(pm, self.a).status, "present", "a morning absence does not cover the afternoon")

    def test_unjustified_absence_tells_the_family_once_a_day(self):
        am = self._session(slot="am")
        self._line(am, self.a).status = "absent"
        am.action_done()
        mails = self._mails_to(self.p.partner_id)
        self.assertEqual(len(mails), 1)
        self.assertIn("Alpha Essai", mails.subject)
        self.assertFalse(self._mails_to(self.quiet), "an adult who receives no notices is not told")
        pm = self._session(slot="pm")
        self._line(pm, self.a).status = "absent"
        pm.action_done()
        self.assertEqual(len(self._mails_to(self.p.partner_id)), 1, "once a day, not once per roll call")

    def test_justified_absence_sends_nothing(self):
        am = self._session()
        self._line(am, self.a).write({"status": "absent", "reason_id": self.illness.id})
        am.action_done()
        self.assertFalse(self._mails_to(self.p.partner_id))

    def test_late_declaration_justifies_the_roll_call(self):
        am = self._session()
        self._line(am, self.a).status = "absent"
        am.action_done()
        self.assertFalse(self._line(am, self.a).justified)
        self.env["bf.school.absence.declaration"].create({
            "student_id": self.a.id, "date_from": self.today, "date_to": self.today, "reason_id": self.illness.id})
        self.assertTrue(self._line(am, self.a).justified)

    def test_slots_follow_the_group_mode(self):
        with self.assertRaises(ValidationError):
            self._session(slot="3")
        with self.assertRaises(ValidationError):
            self._session(group=self.g5, slot="am")
        with self.assertRaises(ValidationError):
            self._session(group=self.g5, slot="5")
        self.assertTrue(self._session(group=self.g5, slot="4"))

    def test_half_day_declaration_is_one_day(self):
        with self.assertRaises(ValidationError):
            self.env["bf.school.absence.declaration"].create({
                "student_id": self.a.id, "date_from": self.today, "date_to": self.today + timedelta(days=1),
                "part": "am", "reason_id": self.illness.id})

    def test_teacher_takes_only_own_groups(self):
        session = self._session(user=self.t1)
        self.assertEqual(session.teacher_id, self.t1)
        with self.assertRaises(AccessError):
            self._session(group=self.g5, slot="1", user=self.t1)
        self.assertFalse(self.env["bf.school.attendance.session"].with_user(self.t2).search(
            [("id", "=", session.id)]))

    def test_repeated_unjustified(self):
        for days_ago in (1, 2, 3):
            session = self._session(day=self.today - timedelta(days=days_ago))
            self._line(session, self.a).status = "absent"
            self._line(session, self.b).status = "absent" if days_ago < 3 else "present"
        # Bravo is also absent the afternoon of day 1: three roll calls, still two DAYS.
        pm = self._session(slot="pm", day=self.today - timedelta(days=1))
        self._line(pm, self.b).status = "absent"
        counts = self.env["bf.school.attendance.line"]._repeated_unjustified()
        self.assertEqual(counts, {self.a: 3}, "three distinct days for Alpha, two for Bravo")


@tagged("post_install", "-at_install")
class TestAttendancePortal(AttendanceCase):

    def test_parent_declares_an_absence(self):
        self.authenticate("school_att_p", "school_att_p")
        page = "/my/school/attendance"
        response = self.url_open(page + "/declare", data={
            "csrf_token": self._csrf(page), "student_id": self.a.id,
            "date_from": str(self.today + timedelta(days=2)), "date_to": str(self.today + timedelta(days=2)),
            "part": "full", "reason_id": self.illness.id, "comment": "Rendez-vous"})
        self.assertIn("the absence is recorded", response.text)
        declaration = self.env["bf.school.absence.declaration"].search([("student_id", "=", self.a.id)])
        self.assertEqual((declaration.source, declaration.declared_by_id), ("portal", self.p.partner_id))

    def test_parent_cannot_declare_for_another_child(self):
        self.authenticate("school_att_p", "school_att_p")
        page = "/my/school/attendance"
        response = self.url_open(page + "/declare", data={
            "csrf_token": self._csrf(page), "student_id": self.b.id, "date_from": str(self.today),
            "reason_id": self.illness.id})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.env["bf.school.absence.declaration"].search([("student_id", "=", self.b.id)]))

    def test_notices_without_authority_do_not_declare(self):
        """Adversarial review (2026-09-27): declaring commits the child."""
        step = new_test_user(self.env, login="school_att_step", email="att.step@example.invalid",
                             groups="base.group_portal")
        self.env["bf.school.guardian.link"].create({"student_id": self.a.id, "guardian_id": step.partner_id.id,
                                                    "relationship": "other", "receives_notices": True})
        self.authenticate("school_att_step", "school_att_step")
        page = "/my/school/attendance"
        self.assertNotIn("Declare an absence", self.url_open(page).text)
        response = self.url_open(page + "/declare", data={
            "csrf_token": self._csrf("/my/account"), "student_id": self.a.id, "date_from": str(self.today),
            "reason_id": self.illness.id})
        self.assertEqual(response.status_code, 404)

    def test_too_old_goes_to_the_office(self):
        self.authenticate("school_att_p", "school_att_p")
        page = "/my/school/attendance"
        response = self.url_open(page + "/declare", data={
            "csrf_token": self._csrf(page), "student_id": self.a.id,
            "date_from": str(self.today - timedelta(days=45)), "reason_id": self.illness.id})
        self.assertIn("contact the school office", response.text)
        self.assertFalse(self.env["bf.school.absence.declaration"].search([("student_id", "=", self.a.id)]))

    def test_parent_sees_own_child_only(self):
        for child in (self.a, self.b):
            session = self.env["bf.school.attendance.session"].search(
                [("group_id", "=", self.g301.id), ("date", "=", self.today), ("slot", "=", "am")]) or self._session()
            self._line(session, child).status = "absent"
        session.action_done()
        self.authenticate("school_att_p", "school_att_p")
        page = self.url_open("/my/school/attendance").text
        self.assertIn("Alpha Essai", page)
        self.assertNotIn("Bravo Essai", page)
        self.assertIn("To justify", page)

    def test_portal_has_no_rpc_access(self):
        with self.assertRaises(AccessError):
            self.env["bf.school.attendance.line"].with_user(self.p).search([])


@tagged("post_install", "-at_install")
class TestAttendanceFrench(AttendanceCase):

    def test_email_renders_in_french(self):
        """🔴 The French body is a separate translation: a wrong one crashes only in French."""
        self.env["res.lang"]._activate_lang("fr_CA")
        self.p.partner_id.lang = "fr_CA"
        am = self._session()
        self._line(am, self.a).status = "absent"
        am.action_done()
        mail = self._mails_to(self.p.partner_id)
        self.assertEqual(mail.subject, "Alpha Essai : absence aujourd'hui")
        self.assertIn("Motiver l'absence", mail.body_html)

    def test_slot_reads_as_words(self):
        # « am » was shown raw in the roll call and its list (demo École, 2026-10-02).
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["ir.module.module"]._load_module_terms(["bf_school_attendance"], ["fr_CA"])
        am = self._session().with_context(lang="fr_CA")
        self.assertEqual(am.slot_label, "Matin")
        line = self._line(am, self.a)
        self.assertEqual(line.display_name, "Alpha Essai · %s" % am.display_name)
        self.assertIn("matin", line.display_name)
