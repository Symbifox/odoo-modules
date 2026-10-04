import base64
from datetime import date

from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, new_test_user, tagged

PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f"
    b"\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00"
    b"\x00\x00IEND\xaeB`\x82")


@tagged("post_install", "-at_install")
class TestSchoolSlides(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.school = env["bf.school"].create({"name": "École des Essais"})
        Year = env["bf.school.year"]
        cls.year = Year.create({"name": "2026-2027", "school_id": cls.school.id,
                                "date_start": date(2026, 8, 27), "date_end": date(2027, 6, 23)})
        cls.year.action_set_current()
        cls.next_year = Year.create({"name": "2027-2028", "school_id": cls.school.id,
                                     "date_start": date(2027, 8, 26), "date_end": date(2028, 6, 22)})
        cls.t1 = new_test_user(env, login="school_sl_t1", groups="bf_school_core.group_school_user")
        cls.t2 = new_test_user(env, login="school_sl_t2", groups="bf_school_core.group_school_user")
        Group = env["bf.school.group"]
        cls.g1 = Group.create({"name": "301", "school_id": cls.school.id, "year_id": cls.year.id,
                               "teacher_ids": [(6, 0, cls.t1.ids)]})
        cls.g2 = Group.create({"name": "302", "school_id": cls.school.id, "year_id": cls.year.id,
                               "teacher_ids": [(6, 0, cls.t2.ids)]})
        cls.g1_next = Group.create({"name": "401", "school_id": cls.school.id, "year_id": cls.next_year.id,
                                    "teacher_ids": [(6, 0, cls.t1.ids)]})
        Partner = env["res.partner"]
        cls.a = Partner.create({"name": "Alpha Essai", "is_student": True})
        cls.b = Partner.create({"name": "Bravo Essai", "is_student": True})
        cls.c = Partner.create({"name": "Charlie Essai", "is_student": True})
        Enrollment = env["bf.school.enrollment"]
        Enrollment.create({"student_id": cls.a.id, "group_id": cls.g1.id})
        Enrollment.create({"student_id": cls.b.id, "group_id": cls.g2.id})
        Enrollment.create({"student_id": cls.c.id, "group_id": cls.g1_next.id})
        cls.p = new_test_user(env, login="school_sl_p", groups="base.group_portal")
        cls.q = new_test_user(env, login="school_sl_q", groups="base.group_portal")
        cls.r = new_test_user(env, login="school_sl_r", groups="base.group_portal")
        Link = env["bf.school.guardian.link"]
        Link.create({"student_id": cls.a.id, "guardian_id": cls.p.partner_id.id})
        Link.create({"student_id": cls.b.id, "guardian_id": cls.q.partner_id.id})
        # r is linked to Alpha but does not receive the school's notices.
        Link.create({"student_id": cls.a.id, "guardian_id": cls.r.partner_id.id,
                     "relationship": "other", "receives_notices": False,
                     "has_parental_authority": False})
        cls.au = env["res.users"].with_context(no_reset_password=True).create({
            "login": "school_sl_alpha", "password": "school_sl_alpha", "partner_id": cls.a.id,
            "groups_id": [(6, 0, env.ref("base.group_portal").ids)]})

    def _course(self, user=None, **vals):
        Channel = self.env["slide.channel"]
        if user:
            Channel = Channel.with_user(user)
        return Channel.create(dict({
            "name": "Mathématiques 301", "school_group_ids": [(6, 0, self.g1.ids)],
            "visibility": "public", "allow_comment": True}, **vals))

    def _members(self, course, synced=None):
        domain = [("channel_id", "=", course.id)]
        if synced is not None:
            domain.append(("school_synced", "=", synced))
        return self.env["slide.channel.partner"].search(domain).partner_id

    def test_school_course_is_closed_and_quiet(self):
        course = self._course(user=self.t1)
        self.assertEqual((course.visibility, course.enroll, course.allow_comment),
                         ("members", "invite", False))
        self.assertEqual((course.karma_gen_channel_finish, course.karma_gen_channel_rank), (0, 0))
        self.assertFalse(course.completed_template_id)
        course.with_user(self.t1).write({"visibility": "public", "allow_comment": True})
        self.assertEqual((course.visibility, course.allow_comment), ("members", False))
        slide = self.env["slide.slide"].create({"name": "Quiz", "channel_id": course.id,
                                                "slide_category": "quiz"})
        self.assertEqual(slide.quiz_first_attempt_reward, 0)

    def test_attendees_are_the_group(self):
        course = self._course()
        self.assertEqual(self._members(course, synced=True), self.a | self.p.partner_id)
        self.assertNotIn(self.r.partner_id, self._members(course), "no notices, no course")
        followers = course.message_partner_ids
        self.assertIn(self.a, followers)
        self.assertNotIn(self.p.partner_id, followers, "parents get no email at each content")

    def test_attendees_follow_the_enrolments(self):
        course = self._course()
        enrollment = self.env["bf.school.enrollment"].create({"student_id": self.b.id, "group_id": self.g1.id})
        self.assertIn(self.b, self._members(course))
        self.assertIn(self.q.partner_id, self._members(course))
        enrollment.state = "left"
        self.assertNotIn(self.b, course.channel_partner_ids.partner_id)
        self.assertNotIn(self.q.partner_id, course.channel_partner_ids.partner_id)
        course.school_include_guardians = False
        self.assertEqual(course.channel_partner_ids.partner_id, self.a | self.env.user.partner_id)

    def test_hand_invited_attendee_stays(self):
        course = self._course()
        guest = self.env["res.partner"].create({"name": "Orthopédagogue invitée"})
        course._action_add_members(guest)
        course.school_group_ids = [(6, 0, self.g2.ids)]
        members = course.channel_partner_ids.partner_id
        self.assertIn(guest, members)
        self.assertIn(self.b, members)
        self.assertNotIn(self.a, members)

    def test_teacher_ties_own_groups_only(self):
        with self.assertRaises(AccessError):
            self._course(user=self.t2)
        course = self._course(user=self.t1)
        with self.assertRaises(AccessError):
            course.with_user(self.t1).write({"school_group_ids": [(4, self.g2.id)]})

    def test_portal_lists_and_opens_the_course(self):
        course = self._course(website_published=True)
        self.env["slide.slide"].create({"name": "Les fractions", "channel_id": course.id,
                                        "slide_category": "article", "html_content": "<p>Moitié</p>",
                                        "is_published": True})
        self.authenticate("school_sl_p", "school_sl_p")
        self.assertIn("Mathématiques 301", self.url_open("/my/school/courses").text)
        self.assertIn("Les fractions", self.url_open(course.website_url).text)
        self.authenticate("school_sl_q", "school_sl_q")
        self.assertNotIn("Mathématiques 301", self.url_open("/my/school/courses").text)
        self.assertNotIn("Les fractions", self.url_open(course.website_url).text)
        self.authenticate("school_sl_alpha", "school_sl_alpha")
        self.assertIn("Mathématiques 301", self.url_open("/my/school/courses").text)

    def test_renew_copies_contents_for_next_year(self):
        course = self._course(user=self.t1, website_published=True)
        slide = self.env["slide.slide"].create({
            "name": "Schéma", "channel_id": course.id, "slide_category": "infographic",
            "source_type": "local_file", "image_binary_content": PNG, "is_published": True,
            "slide_resource_ids": [(0, 0, {"resource_type": "file", "name": "Exercices",
                                           "data": PNG, "file_name": "exercices.png"})]})
        wizard = self.env["bf.school.slide.renew"].with_user(self.t1).create({
            "channel_id": course.id, "name": "Mathématiques 401", "group_ids": [(6, 0, self.g1_next.ids)]})
        copy = self.env["slide.channel"].browse(wizard.action_renew()["res_id"])
        self.assertEqual(copy.name, "Mathématiques 401")
        self.assertFalse(copy.website_published)
        self.assertEqual(copy.user_id, self.t1)
        self.assertEqual(copy.visibility, "members")
        copied = copy.slide_ids
        self.assertEqual(copied.mapped("name"), ["Schéma"])
        self.assertEqual(copied.binary_content, slide.binary_content)
        self.assertEqual(copied.slide_resource_ids.data, slide.slide_resource_ids.data)
        self.assertEqual(self._members(copy, synced=True), self.c)
        self.assertEqual(self._members(course, synced=True), self.a | self.p.partner_id, "the old course stays")

    def test_student_profile_not_published(self):
        # Refused whoever writes it (sudo too): the student's own write is also stopped by
        # bf_school_student_portal, which this test must not lean on.
        with self.assertRaises(UserError):
            self.au.sudo().write({"website_published": True})
        self.p.with_user(self.p).write({"website_published": True})

    # ---------------------------------------------------------------- review, 2026-10-03

    def test_no_email_nor_back_door(self):
        course = self._course(user=self.t1)
        self.assertFalse(course.publish_template_id, "no email to students at each content")
        portal = self.env.ref("base.group_portal")
        course.with_user(self.t1).write({"enroll_group_ids": [(4, portal.id)],
                                         "upload_group_ids": [(4, portal.id)]})
        self.assertFalse(course.enroll_group_ids | course.upload_group_ids)
        self.assertEqual(self._members(course, synced=True), self.a | self.p.partner_id)
        slide = self.env["slide.slide"].create({"name": "Quiz", "channel_id": course.id,
                                                "slide_category": "quiz"})
        slide.with_user(self.t1).write({"quiz_first_attempt_reward": 10})
        self.assertEqual(slide.quiz_first_attempt_reward, 0)

    def test_no_karma_for_students(self):
        self.au._add_karma(10, reason="essai")
        self.assertEqual(self.au.karma, 0)
        self.p._add_karma(3, reason="essai")
        self.assertEqual(self.p.karma, 3, "parents and staff keep Odoo's karma")

    def test_deleted_group_takes_its_attendees(self):
        course = self._course()
        self.assertIn(self.a, self._members(course))
        self.g1.unlink()
        self.assertFalse(course.channel_partner_ids.filtered("school_synced"))

    def test_only_the_responsible_renews(self):
        course = self._course(user=self.t1)
        wizard = self.env["bf.school.slide.renew"].with_user(self.t2).create({
            "channel_id": course.id, "name": "Copie", "group_ids": [(6, 0, self.g2.ids)]})
        with self.assertRaises(UserError):
            wizard.action_renew()
