import re
from datetime import date, datetime, timedelta

from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class MeetingCase(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.company.partner_id.tz = "America/Toronto"
        cls.school = env["bf.school"].create({"name": "École des Essais"})
        year = env["bf.school.year"].create({
            "name": "2026-2027", "school_id": cls.school.id,
            "date_start": date(2026, 8, 27), "date_end": date(2027, 6, 23)})
        year.action_set_current()
        mk = lambda login, name: new_test_user(env, login=login, name=name, groups="bf_school_core.group_school_user")
        cls.t1, cls.t2, cls.t3 = mk("school_mtg_t1", "Prof Un"), mk("school_mtg_t2", "Prof Deux"), mk("school_mtg_t3", "Prof Trois")
        Group = env["bf.school.group"]
        cls.g301 = Group.create({"name": "301", "school_id": cls.school.id, "year_id": year.id,
                                 "teacher_ids": [(6, 0, (cls.t1 | cls.t2).ids)]})
        cls.g302 = Group.create({"name": "302", "school_id": cls.school.id, "year_id": year.id,
                                 "teacher_ids": [(6, 0, cls.t3.ids)]})
        Partner = env["res.partner"]
        cls.a = Partner.create({"name": "Alpha Essai", "is_student": True})
        cls.b = Partner.create({"name": "Bravo Essai", "is_student": True})
        cls.c = Partner.create({"name": "Charlie Essai", "is_student": True})
        Enrollment = env["bf.school.enrollment"]
        for child, group in ((cls.a, cls.g301), (cls.b, cls.g302), (cls.c, cls.g301)):
            Enrollment.create({"student_id": child.id, "group_id": group.id})
        cls.p = new_test_user(env, login="school_mtg_p", name="Parent P", email="mtg.p@example.invalid",
                              groups="base.group_portal")
        cls.q = new_test_user(env, login="school_mtg_q", name="Parent Q", email="mtg.q@example.invalid",
                              groups="base.group_portal")
        Link = env["bf.school.guardian.link"]
        for child in cls.a | cls.b:
            Link.create({"student_id": child.id, "guardian_id": cls.p.partner_id.id})
        Link.create({"student_id": cls.c.id, "guardian_id": cls.q.partner_id.id})
        now = datetime.now().replace(microsecond=0)
        # 22:00 UTC on 2026-10-15 is 18:00 in Montréal (EDT).
        cls.start = datetime(2026, 10, 15, 22, 0)
        cls.meeting = env["bf.school.meeting.session"].create({
            "name": "Rencontres de la 1re étape", "school_id": cls.school.id,
            "group_ids": [(6, 0, (cls.g301 | cls.g302).ids)], "slot_minutes": 10, "location": "Salle 12",
            "booking_open": now - timedelta(hours=1), "booking_close": now + timedelta(days=2),
            "availability_ids": [(0, 0, {"teacher_id": t.id, "start": cls.start, "stop": cls.start + timedelta(minutes=30)})
                                 for t in (cls.t1, cls.t2, cls.t3)]})
        cls.meeting.action_generate_slots()
        cls.meeting.action_open()

    # 🔴 Not `session`: HttpCase keeps its HTTP session there, and `authenticate()` broke.
    def slot(self, teacher, minutes=0):
        return self.meeting.slot_ids.filtered(
            lambda s: s.teacher_id == teacher and s.start == self.start + timedelta(minutes=minutes))

    def _csrf(self, url):
        page = self.url_open(url).text
        match = re.search(r'name="csrf_token" value="([^"]+)"', page) or re.search(
            r'csrf_token["\']?\s*:\s*["\']([^"\']+)', page)
        self.assertTrue(match)
        return match.group(1)


@tagged("post_install", "-at_install")
class TestMeetingLogic(MeetingCase):

    def test_slots_generated_and_booked_ones_kept(self):
        self.assertEqual(len(self.meeting.slot_ids), 9, "three teachers, three slots of 10 minutes")
        self.slot(self.t1)._school_book(self.a, self.p.partner_id)
        self.meeting.action_generate_slots()
        self.assertEqual(len(self.meeting.slot_ids), 9)
        self.assertEqual(self.slot(self.t1).student_id, self.a, "a booked slot survives regeneration")

    def test_offers_follow_the_children_groups(self):
        Session = self.env["bf.school.meeting.session"]
        offers_p = {(s.id, st, t) for s, st, t in Session._school_offers_for(self.p.partner_id)}
        self.assertEqual(offers_p, {(self.meeting.id, self.a, self.t1), (self.meeting.id, self.a, self.t2),
                                    (self.meeting.id, self.b, self.t3)})
        offers_q = {(st, t) for _s, st, t in Session._school_offers_for(self.q.partner_id)}
        self.assertEqual(offers_q, {(self.c, self.t1), (self.c, self.t2)})

    def test_session_limited_to_its_groups(self):
        """A session for the 301 only offers nothing for a child of the 302."""
        only_301 = self.env["bf.school.meeting.session"].create({
            "name": "Rencontres du 301", "school_id": self.school.id, "group_ids": [(6, 0, self.g301.ids)],
            "booking_open": self.meeting.booking_open, "booking_close": self.meeting.booking_close,
            "availability_ids": [(0, 0, {"teacher_id": self.t3.id, "start": self.start,
                                         "stop": self.start + timedelta(minutes=10)})]})
        only_301.action_generate_slots()
        only_301.action_open()
        offers = self.env["bf.school.meeting.session"]._school_offers_for(self.p.partner_id)
        self.assertFalse([o for o in offers if o[0] == only_301 and o[1] == self.b])

    def test_no_overlap_for_one_adult(self):
        self.slot(self.t1)._school_book(self.a, self.p.partner_id)
        with self.assertRaises(UserError):
            self.slot(self.t2)._school_book(self.a, self.p.partner_id)
        self.slot(self.t2, 10)._school_book(self.a, self.p.partner_id)
        with self.assertRaises(UserError):
            self.slot(self.t3)._school_book(self.b, self.p.partner_id)

    def test_slot_taken_by_another_family(self):
        self.slot(self.t1)._school_book(self.a, self.p.partner_id)
        with self.assertRaises(UserError):
            self.slot(self.t1)._school_book(self.c, self.q.partner_id)

    def test_one_meeting_per_child_and_teacher(self):
        self.slot(self.t1)._school_book(self.a, self.p.partner_id)
        with self.assertRaises(UserError):
            self.slot(self.t1, 20)._school_book(self.a, self.p.partner_id)

    def test_teacher_must_teach_the_child(self):
        with self.assertRaises(UserError):
            self.slot(self.t3)._school_book(self.a, self.p.partner_id)
        with self.assertRaises(UserError):
            self.slot(self.t1)._school_book(self.c, self.p.partner_id)

    def test_booking_closed(self):
        self.meeting.action_close()
        with self.assertRaisesRegex(UserError, "Booking is closed"):
            self.slot(self.t1)._school_book(self.a, self.p.partner_id)

    def test_cancel_by_the_booking_adult_only(self):
        slot = self.slot(self.t1)
        slot._school_book(self.a, self.p.partner_id)
        with self.assertRaises(UserError):
            slot._school_cancel(self.q.partner_id)
        slot._school_cancel(self.p.partner_id)
        self.assertFalse(slot.student_id)

    def test_confirmation_email_in_local_time(self):
        self.slot(self.t1)._school_book(self.a, self.p.partner_id)
        mail = self.env["mail.mail"].sudo().search([("recipient_ids", "in", self.p.partner_id.ids)])
        self.assertEqual(len(mail), 1)
        self.assertIn("18:00", mail.body_html, "Montréal time, not UTC")
        self.assertIn("Salle 12", mail.body_html)

    def test_teacher_reads_own_slots_only(self):
        slots = self.env["bf.school.meeting.slot"].with_user(self.t1).search([])
        self.assertEqual(slots.teacher_id, self.t1)
        with self.assertRaises(AccessError):
            self.slot(self.t1).with_user(self.t1).write({"student_id": self.a.id})

    def test_portal_user_has_no_rpc_access(self):
        with self.assertRaises(AccessError):
            self.env["bf.school.meeting.slot"].with_user(self.p).search([])


@tagged("post_install", "-at_install")
class TestMeetingPortal(MeetingCase):

    def test_parent_books_through_the_page(self):
        self.authenticate("school_mtg_p", "school_mtg_p")
        page = self.url_open("/my/school/meetings").text
        for name in ("Prof Un", "Prof Deux", "Prof Trois", "Alpha Essai", "Bravo Essai"):
            self.assertIn(name, page)
        self.assertIn("18:00", page, "slots in Montréal time")
        self.assertNotIn("22:00", page)
        self.assertNotIn("Charlie Essai", page)
        self.url_open("/my/school/meetings/book", data={
            "csrf_token": self._csrf("/my/school/meetings"),
            "slot_id": self.slot(self.t1).id, "student_id": self.a.id})
        self.assertEqual(self.slot(self.t1).student_id, self.a)
        self.assertIn("My meetings", self.url_open("/my/school/meetings").text)

    def test_error_comes_back_on_the_page(self):
        self.slot(self.t1)._school_book(self.c, self.q.partner_id)
        self.authenticate("school_mtg_p", "school_mtg_p")
        # The POST follows its redirect: that page shows the message, once.
        response = self.url_open("/my/school/meetings/book", data={
            "csrf_token": self._csrf("/my/school/meetings"),
            "slot_id": self.slot(self.t1).id, "student_id": self.a.id})
        self.assertIn("just taken", response.text)
        self.assertNotIn("just taken", self.url_open("/my/school/meetings").text, "shown once")

    def test_other_family_cannot_book_for_my_child(self):
        self.authenticate("school_mtg_q", "school_mtg_q")
        self.url_open("/my/school/meetings/book", data={
            "csrf_token": self._csrf("/my/school/meetings"),
            "slot_id": self.slot(self.t1).id, "student_id": self.a.id})
        self.assertFalse(self.slot(self.t1).student_id)


@tagged("post_install", "-at_install")
class TestMeetingFrench(MeetingCase):

    def test_email_renders_in_french(self):
        """🔴 The French body is a separate translation: a wrong one crashes only in French."""
        self.env["res.lang"]._activate_lang("fr_CA")
        self.p.partner_id.lang = "fr_CA"
        self.slot(self.t1)._school_book(self.a, self.p.partner_id)
        mail = self.env["mail.mail"].sudo().search([("recipient_ids", "in", self.p.partner_id.ids)])
        self.assertEqual(mail.subject, "Alpha Essai : rencontre avec Prof Un")
        self.assertIn("Votre rencontre est réservée", mail.body_html)
        self.assertIn("18:00", mail.body_html)
