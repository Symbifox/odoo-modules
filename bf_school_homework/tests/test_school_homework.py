from datetime import date, timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSchoolHomework(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.school = env["bf.school"].create({"name": "École des Essais"})
        year = env["bf.school.year"].create({"name": "2026-2027", "school_id": cls.school.id,
                                             "date_start": date(2026, 8, 27), "date_end": date(2027, 6, 23)})
        year.action_set_current()
        cls.t1 = new_test_user(env, login="school_hw_t1", groups="bf_school_core.group_school_user")
        cls.t2 = new_test_user(env, login="school_hw_t2", groups="bf_school_core.group_school_user")
        Group = env["bf.school.group"]
        cls.g1 = Group.create({"name": "301", "school_id": cls.school.id, "year_id": year.id,
                               "teacher_ids": [(6, 0, cls.t1.ids)]})
        cls.g2 = Group.create({"name": "302", "school_id": cls.school.id, "year_id": year.id,
                               "teacher_ids": [(6, 0, cls.t2.ids)]})
        Partner = env["res.partner"]
        cls.a = Partner.create({"name": "Alpha Essai", "is_student": True})
        cls.b = Partner.create({"name": "Bravo Essai", "is_student": True})
        env["bf.school.enrollment"].create({"student_id": cls.a.id, "group_id": cls.g1.id})
        env["bf.school.enrollment"].create({"student_id": cls.b.id, "group_id": cls.g2.id})
        cls.p = new_test_user(env, login="school_hw_p", groups="base.group_portal")
        cls.q = new_test_user(env, login="school_hw_q", groups="base.group_portal")
        env["bf.school.guardian.link"].create({"student_id": cls.a.id, "guardian_id": cls.p.partner_id.id})
        env["bf.school.guardian.link"].create({"student_id": cls.b.id, "guardian_id": cls.q.partner_id.id})
        cls.today = fields.Date.context_today(env["res.partner"])

    def _hw(self, group=None, user=None, **vals):
        Homework = self.env["bf.school.homework"]
        if user:
            Homework = Homework.with_user(user)
        return Homework.create(dict({"name": "Lire le chapitre 4, puis résumer", "group_id": (group or self.g1).id,
                                     "date_due": self.today + timedelta(days=3)}, **vals))

    def test_teacher_posts_for_own_groups_only(self):
        self.assertEqual(self._hw(user=self.t1).teacher_id, self.t1)
        with self.assertRaises(AccessError):
            self._hw(group=self.g2, user=self.t1)

    def test_due_after_given(self):
        with self.assertRaises(ValidationError):
            self._hw(date_due=self.today - timedelta(days=1))

    def test_family_gets_own_children_only(self):
        mine, theirs = self._hw(), self._hw(group=self.g2, name="Autre groupe")
        pairs = self.env["bf.school.homework"]._school_for_partner(self.p.partner_id)
        self.assertEqual(pairs, [(mine, self.a)])
        self.assertNotIn(theirs, [h for h, _c in pairs])

    def test_portal_page(self):
        self._hw()
        self.authenticate("school_hw_p", "school_hw_p")
        page = self.url_open("/my/school/homework").text
        self.assertIn("Lire le chapitre 4", page)
        self.assertIn(".ics", page)
        self.assertNotIn("Autre groupe", page)

    def test_ical_feed(self):
        self._hw(name="Projet, étape 1; brouillon",
                 description="<p>%s</p>" % ("Relire les consignes du projet et préparer le plan détaillé. " * 4))
        self._hw(group=self.g2, name="Autre groupe")
        partner = self.p.partner_id
        response = self.url_open("/school/homework/%s/%s.ics" % (partner.id, partner._school_ical_token()))
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/calendar", response.headers["Content-Type"])
        body = response.content.decode()
        self.assertIn("DTSTART;VALUE=DATE:%s" % (self.today + timedelta(days=3)).strftime("%Y%m%d"), body)
        self.assertIn("Projet\\, étape 1\\; brouillon", body, "RFC 5545 escaping")
        self.assertNotIn("Autre groupe", body)
        lines = body.split("\r\n")
        self.assertTrue(any(line.startswith(" ") for line in lines), "the long description is folded")
        for line in lines:
            self.assertLessEqual(len(line.encode()), 75, "RFC 5545 folding")

    def test_wrong_or_renewed_token_is_404(self):
        partner = self.p.partner_id
        old = partner._school_ical_token()
        self.assertEqual(self.url_open("/school/homework/%s/%s.ics" % (partner.id, "x" * 43)).status_code, 404)
        partner._school_ical_reset()
        self.assertEqual(self.url_open("/school/homework/%s/%s.ics" % (partner.id, old)).status_code, 404)
        self.assertEqual(self.url_open("/school/homework/%s/%s.ics" % (
            partner.id, partner._school_ical_token())).status_code, 200)

    def test_portal_has_no_rpc_access(self):
        with self.assertRaises(AccessError):
            self.env["bf.school.homework"].with_user(self.p).search([])

    def test_teacher_moves_a_homework_to_own_groups_only(self):
        """The record rule reads the homework where it is, not where it goes (review, 2026-10-04)."""
        homework = self._hw(user=self.t1)
        with self.assertRaises(AccessError):
            homework.with_user(self.t1).group_id = self.g2
        self.assertEqual(homework.group_id, self.g1)

    # Creation guards (2026-10-08)
    def test_who_gave_it_is_the_person_recording(self):
        self.assertEqual(self._hw(user=self.t1, teacher_id=self.t2.id).teacher_id, self.t1)
        homework = self.env["bf.school.homework"].with_user(self.t1).with_context(
            default_teacher_id=self.t2.id).create({"name": "Essai", "group_id": self.g1.id,
                                                   "date_due": self.today + timedelta(days=3)})
        self.assertEqual(homework.teacher_id, self.t1)
        with self.assertRaises(UserError):
            homework.write({"teacher_id": self.t2.id})
