import base64
import re
from datetime import date, datetime, timedelta, timezone

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.bf_school_timetable.models import ics
from odoo.addons.bf_school_timetable.models.timetable import float_to_time


class TimetableCase(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.today = fields.Date.context_today(env["res.partner"])
        cls.school = env["bf.school"].create({"name": "École des Essais"})
        cls.year = env["bf.school.year"].create({
            "name": "2026-2027", "school_id": cls.school.id,
            "date_start": cls.today - timedelta(days=60), "date_end": cls.today + timedelta(days=200)})
        cls.year.action_set_current()
        cls.office = new_test_user(env, login="tt_office", groups="bf_school_core.group_school_manager")
        cls.t_math = new_test_user(env, login="tt_math", groups="bf_school_core.group_school_user")
        cls.t_pe = new_test_user(env, login="tt_pe", groups="bf_school_core.group_school_user")
        Group = env["bf.school.group"]
        cls.g301 = Group.create({"name": "301", "school_id": cls.school.id, "year_id": cls.year.id})
        cls.g302 = Group.create({"name": "302", "school_id": cls.school.id, "year_id": cls.year.id})
        Partner = env["res.partner"]
        cls.alpha = Partner.create({"name": "Alpha Essai", "is_student": True})
        cls.bravo = Partner.create({"name": "Bravo Essai", "is_student": True})
        env["bf.school.enrollment"].create({"student_id": cls.alpha.id, "group_id": cls.g301.id})
        env["bf.school.enrollment"].create({"student_id": cls.bravo.id, "group_id": cls.g302.id})
        Course = env["bf.school.course"]
        math, pe = env.ref("bf_school_gradebook.subject_el_mat"), env.ref("bf_school_gradebook.subject_el_eps")
        cls.math301 = Course.create({"group_id": cls.g301.id, "subject_id": math.id, "teacher_ids": [(6, 0, cls.t_math.ids)]})
        cls.pe301 = Course.create({"group_id": cls.g301.id, "subject_id": pe.id, "teacher_ids": [(6, 0, cls.t_pe.ids)]})
        cls.math302 = Course.create({"group_id": cls.g302.id, "subject_id": math.id, "teacher_ids": [(6, 0, cls.t_math.ids)]})
        cls.ccq302 = Course.create({"group_id": cls.g302.id, "subject_id": env.ref("bf_school_gradebook.subject_el_ccq").id,
                                    "teacher_ids": [(6, 0, cls.t_pe.ids)]})
        cls.timetable = env["bf.school.timetable"].with_user(cls.office).create({
            "name": "Essai", "school_id": cls.school.id, "year_id": cls.year.id, "cycle_days": 9,
            "date_start": cls.today - timedelta(days=10), "date_end": cls.today + timedelta(days=60)})
        Period = env["bf.school.period"]
        cls.p1 = Period.create({"timetable_id": cls.timetable.id, "name": "Period 1", "start_time": 8.0, "end_time": 9.0})
        cls.p2 = Period.create({"timetable_id": cls.timetable.id, "name": "Period 2", "start_time": 9.0, "end_time": 10.5})
        cls.lunch = Period.create({"timetable_id": cls.timetable.id, "name": "Lunch", "start_time": 12.0, "end_time": 13.0,
                                   "is_break": True})
        cls.room_a = env["bf.school.room"].create({"school_id": cls.school.id, "name": "A-101"})
        cls.room_b = env["bf.school.room"].create({"school_id": cls.school.id, "name": "A-102"})

    def _slot(self, day, period, course, room=None, **extra):
        values = {"timetable_id": self.timetable.id, "day_no": day, "period_id": period.id, "course_id": course.id,
                  "room_id": room.id if room else False}
        values.update(extra)
        return self.env["bf.school.timetable.slot"].with_user(self.office).create(values)

    def _feed(self, student=None, guardian=None):
        guardian = guardian or self.env["res.users"].search([("login", "=", "tt_mother")]).partner_id
        return self.env["bf.school.timetable.feed"]._rotate_for(student or self.alpha, guardian)


@tagged("post_install", "-at_install")
class TestCalendar(TimetableCase):

    def test_the_calendar_has_the_weekdays_numbered_in_a_cycle(self):
        self.timetable.action_generate_days()
        days = self.timetable.day_ids.sorted("date")
        self.assertTrue(all(d.date.weekday() < 5 for d in days))
        self.assertEqual([d.cycle_day for d in days][:11], [1, 2, 3, 4, 5, 6, 7, 8, 9, 1, 2])

    def test_a_holiday_does_not_use_a_day_of_the_cycle(self):
        self.timetable.action_generate_days()
        days = self.timetable.day_ids.sorted("date")
        before = [d.cycle_day for d in days]
        holiday = days[3]
        holiday.kind = "holiday"
        after = [d.cycle_day for d in days.sorted("date")]
        self.assertEqual(after[3], 0)
        self.assertEqual(after[4], before[3], "the day after the holiday takes the day the holiday did not use")
        holiday.kind = "pedagogical"
        self.assertEqual(holiday.cycle_day, 0)
        holiday.kind = "class"
        self.assertEqual([d.cycle_day for d in days.sorted("date")], before)

    def test_the_cycle_starts_where_the_school_says(self):
        self.timetable.first_cycle_day = 4
        self.timetable.action_generate_days()
        self.assertEqual(self.timetable.day_ids.sorted("date")[0].cycle_day, 4)

    def test_a_new_start_or_a_new_length_numbers_the_days_again(self):
        self.timetable.action_generate_days()
        self.timetable.with_user(self.office).first_cycle_day = 4
        days = self.timetable.day_ids.sorted("date")
        self.assertEqual([d.cycle_day for d in days][:7], [4, 5, 6, 7, 8, 9, 1])
        self.timetable.with_user(self.office).write({"first_cycle_day": 1, "cycle_days": 5})
        self.assertEqual([d.cycle_day for d in self.timetable.day_ids.sorted("date")][:7], [1, 2, 3, 4, 5, 1, 2])

    def test_a_shorter_cycle_leaves_no_class_beyond_it(self):
        self._slot(7, self.p1, self.math301)
        with self.assertRaises(ValidationError):
            self.timetable.with_user(self.office).cycle_days = 5

    def test_the_day_of_the_cycle_is_never_typed(self):
        self.timetable.action_generate_days()
        day = self.timetable.day_ids.sorted("date")[0]
        with self.assertRaises(AccessError):
            day.with_user(self.office).cycle_day = 7
        with self.assertRaises(AccessError):
            self.env["bf.school.timetable.day"].with_user(self.office).create({
                "timetable_id": self.timetable.id, "date": self.today - timedelta(days=400), "cycle_day": 3})
        # a context sent by a client does not switch the numbering off
        extra = self.env["bf.school.timetable.day"].with_user(self.office).with_context(skip_renumber=True).create({
            "timetable_id": self.timetable.id, "date": self.timetable.date_end + timedelta(days=3), "kind": "class"})
        self.assertTrue(extra.cycle_day)

    def test_the_office_of_another_school_sees_nothing_of_this_calendar(self):
        self.timetable.action_generate_days()
        other = self.env["res.company"].create({"name": "Autre école inc."})
        stranger = new_test_user(self.env, login="tt_other_office", groups="bf_school_core.group_school_manager",
                                 company_id=other.id, company_ids=[(6, 0, other.ids)])
        for model in ("bf.school.timetable.day", "bf.school.period", "bf.school.timetable.slot", "bf.school.timetable"):
            self.assertFalse(self.env[model].with_user(stranger).with_company(other).search([]), model)
        with self.assertRaises(AccessError):
            self.timetable.with_user(stranger).with_company(other).action_generate_days()

    def test_generating_twice_adds_nothing(self):
        self.timetable.action_generate_days()
        count = len(self.timetable.day_ids)
        self.timetable.action_generate_days()
        self.assertEqual(len(self.timetable.day_ids), count)

    def test_dates_and_cycle_are_checked(self):
        with self.assertRaises(ValidationError):
            self.timetable.date_end = self.timetable.date_start - timedelta(days=1)
        with self.assertRaises(ValidationError):
            self.timetable.first_cycle_day = 10


@tagged("post_install", "-at_install")
class TestSlots(TimetableCase):

    def test_what_is_taught_on_a_day_of_the_cycle_happens_on_its_dates(self):
        self._slot(2, self.p1, self.math301, self.room_a)
        self._slot(2, self.p2, self.pe301, self.room_b)
        self.timetable.action_generate_days()
        group_slots = self.timetable.slot_ids.filtered(lambda s: s.group_id == self.g301)
        days = [d for d in self.timetable.day_ids if d.cycle_day == 2]
        items = self.timetable._occurrences(days[0].date, days[0].date, group_slots)
        self.assertEqual([i["course"].subject_id.code for i in items], ["MAT", "EPS"], "in the order of the day")
        self.assertEqual(items[0]["start"], datetime.combine(days[0].date, datetime.min.time().replace(hour=8)))
        self.assertEqual(items[1]["end"], datetime.combine(days[0].date, datetime.min.time().replace(hour=10, minute=30)))
        self.assertEqual(self.timetable._occurrences(days[0].date, days[0].date,
                                                     self.timetable.slot_ids.filtered(lambda s: s.group_id == self.g302)), [])

    def test_a_teacher_is_not_in_two_places(self):
        self._slot(1, self.p1, self.math301, self.room_a)
        with self.assertRaises(ValidationError):
            self._slot(1, self.p1, self.math302, self.room_b)
        self._slot(1, self.p2, self.math302, self.room_b)

    def test_a_room_is_not_used_twice(self):
        self._slot(1, self.p1, self.math301, self.room_a)
        with self.assertRaises(ValidationError):
            self._slot(1, self.p1, self.ccq302, self.room_a)
        self._slot(1, self.p1, self.ccq302, self.room_b)

    def test_a_group_is_not_in_two_classes_unless_it_splits(self):
        self._slot(1, self.p1, self.math301, self.room_a)
        with self.assertRaises(ValidationError):
            self._slot(1, self.p1, self.pe301, self.room_b)
        first = self.timetable.slot_ids.filtered(lambda s: s.course_id == self.math301)
        first.allow_overlap = True
        with self.assertRaises(ValidationError):
            self._slot(1, self.p1, self.pe301, self.room_b)  # only one side ticked
        self._slot(1, self.p1, self.pe301, self.room_b, allow_overlap=True)

    def test_a_break_holds_no_class(self):
        with self.assertRaises(ValidationError):
            self._slot(1, self.lunch, self.math301)

    def test_the_day_is_in_the_cycle(self):
        with self.assertRaises(ValidationError):
            self._slot(10, self.p1, self.math301)
        with self.assertRaises(ValidationError):
            self._slot(0, self.p1, self.math301)

    def test_the_period_the_course_and_the_room_belong_to_this_timetable_and_school(self):
        other = self.env["bf.school.timetable"].create({
            "name": "Autre", "school_id": self.school.id, "year_id": self.year.id, "date_start": self.today,
            "date_end": self.today + timedelta(days=5)})
        foreign = self.env["bf.school.period"].create({"timetable_id": other.id, "name": "X", "start_time": 8, "end_time": 9})
        with self.assertRaises(ValidationError):
            self._slot(1, foreign, self.math301)
        school2 = self.env["bf.school"].create({"name": "Autre école"})
        room2 = self.env["bf.school.room"].create({"school_id": school2.id, "name": "Z"})
        with self.assertRaises(ValidationError):
            self._slot(1, self.p1, self.math301, room2)

    def test_a_course_of_another_school_year_is_not_in_this_timetable(self):
        next_year = self.env["bf.school.year"].create({
            "name": "2027-2028", "school_id": self.school.id,
            "date_start": self.today + timedelta(days=300), "date_end": self.today + timedelta(days=560)})
        group = self.env["bf.school.group"].create({"name": "401", "school_id": self.school.id, "year_id": next_year.id})
        course = self.env["bf.school.course"].create({
            "group_id": group.id, "subject_id": self.env.ref("bf_school_gradebook.subject_el_mat").id})
        with self.assertRaises(ValidationError):
            self._slot(1, self.p1, course)

    def test_a_period_ends_after_it_starts(self):
        with self.assertRaises(ValidationError):
            self.env["bf.school.period"].create({"timetable_id": self.timetable.id, "name": "Bad", "start_time": 10, "end_time": 9})

    def test_a_period_cannot_end_at_midnight_and_a_time_never_reads_sixty_minutes(self):
        Period = self.env["bf.school.period"]
        for start, end in ((22, 24), (23.5, 23.9999), (-1, 5)):
            with self.assertRaises(ValidationError, msg=(start, end)):
                Period.create({"timetable_id": self.timetable.id, "name": "Bad", "start_time": start, "end_time": end})
        late = Period.create({"timetable_id": self.timetable.id, "name": "Late", "start_time": 22, "end_time": 23.99})
        self.assertEqual((late.start_label, late.end_label), ("22:00", "23:59"))
        self.assertEqual(float_to_time(8.999).strftime("%H:%M"), "09:00")
        self.assertEqual(float_to_time(8.5).strftime("%H:%M"), "08:30")

    def test_publishing_needs_classes_and_a_calendar(self):
        with self.assertRaises(ValidationError):
            self.timetable.action_publish()
        self._slot(1, self.p1, self.math301)
        with self.assertRaises(ValidationError):
            self.timetable.action_publish()
        self.timetable.action_generate_days()
        self.timetable.action_publish()
        self.assertEqual(self.timetable.state, "published")

    def test_teachers_read_the_timetable_and_the_office_writes_it(self):
        self._slot(1, self.p1, self.math301)
        Slot = self.env["bf.school.timetable.slot"].with_user(self.t_math)
        self.assertEqual(len(Slot.search([("timetable_id", "=", self.timetable.id)])), 1)
        with self.assertRaises(AccessError):
            Slot.create({"timetable_id": self.timetable.id, "day_no": 2, "period_id": self.p1.id,
                         "course_id": self.math301.id})

    def test_the_group_timetable_prints(self):
        self._slot(1, self.p1, self.math301, self.room_a)
        self.timetable.action_generate_days()
        self.timetable.action_publish()
        html, _kind = self.env["ir.actions.report"]._render_qweb_html("bf_school_timetable.report_group_timetable", self.g301.ids)
        text = html.decode()
        self.assertIn("Timetable of", text)
        self.assertIn("Mathematics", text)
        self.assertIn("A-101", text)


@tagged("post_install", "-at_install")
class TestIcs(HttpCase):

    def test_text_is_escaped_and_lines_end_with_crlf(self):
        now = datetime(2026, 10, 11, tzinfo=timezone.utc)
        blob = ics.build_ics("Tim, \"table\"; one", [{
            "uid": "1@x", "start": datetime(2026, 10, 12, 12, tzinfo=timezone.utc),
            "end": datetime(2026, 10, 12, 13, tzinfo=timezone.utc), "summary": "Math, level 3; a\\b",
            "location": "A-101", "description": "line one\nline two"}], now=now)
        text = blob.decode()
        self.assertTrue(text.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"))
        self.assertIn("\r\nSUMMARY:Math\\, level 3\\; a\\\\b\r\n", text)
        self.assertIn("DESCRIPTION:line one\\nline two", text)
        self.assertIn("X-WR-CALNAME:Tim\\, \"table\"\\; one", text)
        self.assertIn("DTSTART:20261012T120000Z", text)
        self.assertIn("DTSTAMP:20261011T000000Z", text)
        self.assertTrue(text.endswith("END:VCALENDAR\r\n"))
        self.assertNotIn("\n", text.replace("\r\n", ""))

    def test_long_lines_fold_at_75_octets_without_cutting_a_character(self):
        long_summary = "É" * 80
        blob = ics.build_ics("x", [{
            "uid": "1@x", "start": datetime(2026, 10, 12, 12, tzinfo=timezone.utc),
            "end": datetime(2026, 10, 12, 13, tzinfo=timezone.utc), "summary": long_summary}])
        lines = blob.split(b"\r\n")
        self.assertTrue(all(len(line) <= 75 for line in lines))
        self.assertEqual(blob.decode().replace("\r\n ", "").count("É"), 80)


@tagged("post_install", "-at_install")
class TestFamily(TimetableCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.mother = new_test_user(env, login="tt_mother", name="Maman Essai", email="m.tt@example.invalid",
                                   groups="base.group_portal")
        cls.grandma = new_test_user(env, login="tt_grandma", name="Mamie", email="g.tt@example.invalid",
                                    groups="base.group_portal")
        cls.father = new_test_user(env, login="tt_father", name="Papa Essai", email="p.tt@example.invalid",
                                   groups="base.group_portal")
        cls.stranger = new_test_user(env, login="tt_stranger", name="Etranger", email="s.tt@example.invalid",
                                     groups="base.group_portal")
        Link = env["bf.school.guardian.link"]
        Link.create({"student_id": cls.alpha.id, "guardian_id": cls.mother.partner_id.id, "relationship": "mother"})
        Link.create({"student_id": cls.alpha.id, "guardian_id": cls.father.partner_id.id, "relationship": "father"})
        Link.create({"student_id": cls.alpha.id, "guardian_id": cls.grandma.partner_id.id, "relationship": "grandparent"})
        for day in range(1, 10):
            cls._slot(cls, day, cls.p1, cls.math301, cls.room_a)
        cls.timetable.action_generate_days()

    def test_a_draft_is_not_shown(self):
        self.authenticate("tt_mother", "tt_mother")
        page = self.url_open("/my/school/timetable").text
        self.assertIn("Alpha Essai", page)
        self.assertNotIn("A-101", page, "a draft shows no class")

    def test_a_published_timetable_shows_the_next_seven_days(self):
        self.timetable.action_publish()
        self.authenticate("tt_mother", "tt_mother")
        page = self.url_open("/my/school/timetable").text
        self.assertIn("Alpha Essai", page)
        self.assertIn("A-101", page)

    def test_a_grandparent_without_notices_sees_no_child(self):
        self.timetable.action_publish()
        self.authenticate("tt_grandma", "tt_grandma")
        self.assertNotIn("Alpha Essai", self.url_open("/my/school/timetable").text)

    def test_the_calendar_link_is_made_by_the_family_and_serves_the_childs_classes(self):
        self.timetable.action_publish()
        self.authenticate("tt_mother", "tt_mother")
        page = self.url_open("/my/school/timetable").text
        self.assertNotIn(".ics", page)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        response = self.url_open("/my/school/timetable/link", data={"csrf_token": csrf, "student_id": self.alpha.id})
        self.assertEqual(response.status_code, 200)
        feed = self.env["bf.school.timetable.feed"].sudo().search([("student_id", "=", self.alpha.id)])
        self.assertEqual(feed.guardian_id, self.mother.partner_id)
        self.logout()
        calendar = self.url_open("/school/timetable/%s.ics" % feed.token)
        self.assertEqual(calendar.status_code, 200)
        self.assertIn("text/calendar", calendar.headers["Content-Type"])
        self.assertIn("noindex", calendar.headers["X-Robots-Tag"])
        text = calendar.text
        self.assertEqual(text.count("BEGIN:VEVENT") > 20, True)
        self.assertIn("SUMMARY:Mathematics", text)
        self.assertIn("LOCATION:A-101", text)
        self.assertNotIn("Bravo", text)
        self.assertIn("X-WR-CALNAME:Alpha: timetable", text, "the first name only: the calendar sits on the parent's phone")
        self.assertNotIn("Essai", text)
        self.assertTrue(feed.last_read_on)

    def test_each_adult_has_their_own_link_and_replacing_one_stops_no_other(self):
        self.timetable.action_publish()
        mine = self._feed().token
        theirs = self._feed(guardian=self.father.partner_id).token
        self.assertNotEqual(mine, theirs)
        self._feed()  # the mother replaces hers
        self.assertEqual(self.url_open("/school/timetable/%s.ics" % mine).status_code, 404)
        self.assertEqual(self.url_open("/school/timetable/%s.ics" % theirs).status_code, 200)

    def test_the_link_stops_when_the_adult_no_longer_receives_the_notices(self):
        self.timetable.action_publish()
        feed = self._feed()
        self.assertEqual(self.url_open("/school/timetable/%s.ics" % feed.token).status_code, 200)
        link = self.env["bf.school.guardian.link"].search([("student_id", "=", self.alpha.id),
                                                           ("guardian_id", "=", self.mother.partner_id.id)])
        link.receives_notices = False
        self.assertEqual(self.url_open("/school/timetable/%s.ics" % feed.token).status_code, 404)
        link.receives_notices = True
        self.assertEqual(self.url_open("/school/timetable/%s.ics" % feed.token).status_code, 200)
        link.unlink()
        self.assertEqual(self.url_open("/school/timetable/%s.ics" % feed.token).status_code, 404)

    def test_the_link_stops_when_the_adult_can_no_longer_sign_in(self):
        self.timetable.action_publish()
        feed = self._feed()
        self.mother.active = False
        self.assertEqual(self.url_open("/school/timetable/%s.ics" % feed.token).status_code, 404)

    def test_the_link_stops_and_the_page_hides_the_child_when_the_student_comes_of_age(self):
        self.timetable.action_publish()
        feed = self._feed()
        self.alpha.student_birthdate = self.today - timedelta(days=365 * 17)
        self.assertEqual(self.url_open("/school/timetable/%s.ics" % feed.token).status_code, 200)
        self.alpha.student_birthdate = self.today - timedelta(days=365 * 19)
        self.assertEqual(self.url_open("/school/timetable/%s.ics" % feed.token).status_code, 404)
        self.authenticate("tt_mother", "tt_mother")
        self.assertNotIn("Alpha Essai", self.url_open("/my/school/timetable").text)

    def test_nobody_but_the_system_reads_the_token(self):
        feed = self._feed()
        with self.assertRaises(AccessError):
            feed.with_user(self.office).read(["token"])

    def test_replacing_the_link_stops_the_old_one(self):
        self.timetable.action_publish()
        old = self._feed().token
        new = self._feed().token
        self.assertNotEqual(old, new)
        self.assertEqual(self.url_open("/school/timetable/%s.ics" % old).status_code, 404)
        self.assertEqual(self.url_open("/school/timetable/%s.ics" % new).status_code, 200)
        self.assertEqual(self.url_open("/school/timetable/not-a-token.ics").status_code, 404)

    def test_a_stranger_cannot_make_a_link_for_a_child_that_is_not_theirs(self):
        self.timetable.action_publish()
        self.authenticate("tt_stranger", "tt_stranger")
        # The stranger has no child, so their timetable page has no form: a token from another portal page, as a real
        # attacker would take it, so that the refusal is the controller's and not the CSRF check's.
        page = self.url_open("/my/account").text
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page)
        self.assertTrue(csrf, "a portal page with a form")
        response = self.url_open("/my/school/timetable/link", data={"csrf_token": csrf.group(1), "student_id": self.alpha.id})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.env["bf.school.timetable.feed"].sudo().search([("student_id", "=", self.alpha.id)]))

    def test_each_adult_sees_their_own_address_on_their_own_page(self):
        self.timetable.action_publish()
        mine = self._feed().token
        theirs = self._feed(guardian=self.father.partner_id).token
        self.authenticate("tt_father", "tt_father")
        page = self.url_open("/my/school/timetable").text
        self.assertIn(theirs, page)
        self.assertNotIn(mine, page, "the mother's address is the mother's")

    def test_the_link_is_not_made_by_the_orm(self):
        with self.assertRaises(AccessError):
            self.env["bf.school.timetable.feed"].with_user(self.office).create({
                "student_id": self.alpha.id, "guardian_id": self.mother.partner_id.id})

    def test_an_unpublished_timetable_gives_an_empty_calendar(self):
        feed = self._feed()
        calendar = self.url_open("/school/timetable/%s.ics" % feed.token)
        self.assertEqual(calendar.text.count("BEGIN:VEVENT"), 0)


@tagged("post_install", "-at_install")
class TestImport(TimetableCase):

    def _import(self, text, encoding="utf-8", **extra):
        wizard = self.env["bf.school.timetable.import"].with_user(self.office).create(dict({
            "timetable_id": self.timetable.id, "file": base64.b64encode(text.encode(encoding)), "file_name": "edt.csv"}, **extra))
        wizard.action_import()
        return wizard

    def test_classes_load_from_a_file_and_rooms_are_made(self):
        wizard = self._import("group,subject,day,period,room\n301,MAT,1,Period 1,Salle A\n301,Physical education and health,1,2,Gym\n")
        self.assertIn("2 class(es) loaded", wizard.log)
        slots = self.timetable.slot_ids
        self.assertEqual(sorted(slots.mapped("course_id.subject_id.code")), ["EPS", "MAT"])
        self.assertEqual(sorted(slots.mapped("room_id.name")), ["Gym", "Salle A"])
        self.assertEqual(slots.filtered(lambda s: s.course_id == self.pe301).period_id, self.p2, "a period by its number")

    def test_semicolons_french_headings_and_latin_1(self):
        wizard = self._import("classe;matière;jour;période;salle\n301;MAT;3;1;Salle É\n", encoding="latin-1")
        self.assertIn("1 class(es) loaded", wizard.log)
        self.assertEqual(self.timetable.slot_ids.room_id.name, "Salle É")

    def test_every_line_that_cannot_be_placed_is_said_and_the_others_go_in(self):
        wizard = self._import("group,subject,day,period\n301,MAT,1,1\n999,MAT,1,1\n301,NOPE,1,1\n301,EPS,12,1\n301,EPS,2,Nowhere\n")
        self.assertEqual(len(self.timetable.slot_ids), 1)
        for expected in ("Line 3", "Line 4", "Line 5", "Line 6"):
            self.assertIn(expected, wizard.log)

    def test_a_conflict_is_reported_not_swallowed(self):
        wizard = self._import("group,subject,day,period\n301,MAT,1,1\n302,MAT,1,1\n")
        self.assertEqual(len(self.timetable.slot_ids), 1)
        self.assertIn("already teaching", wizard.log)

    def test_replace_clears_the_groups_in_the_file(self):
        self._slot(5, self.p1, self.math301)
        self._import("group,subject,day,period\n301,MAT,1,1\n", replace=True)
        self.assertEqual(self.timetable.slot_ids.mapped("day_no"), [1])

    def test_replacing_is_all_or_nothing(self):
        self._slot(5, self.p1, self.math301)
        wizard = self._import("group,subject,day,period\n301,MAT,1,1\n301,NOPE,2,1\n", replace=True)
        self.assertEqual(self.timetable.slot_ids.mapped("day_no"), [5], "a line that cannot be placed replaces nothing")
        self.assertIn("Nothing was replaced", wizard.log)
        self.assertIn("Line 3", wizard.log)
        # a conflict found while loading undoes the whole replacement too
        wizard = self._import("group,subject,day,period\n301,MAT,1,1\n302,MAT,1,1\n", replace=True)
        self.assertEqual(self.timetable.slot_ids.mapped("day_no"), [5])
        self.assertIn("Nothing was replaced", wizard.log)
        self.assertIn("already teaching", wizard.log)

    def test_a_room_is_found_by_its_name_not_by_a_wildcard(self):
        self.env["bf.school.room"].create({"school_id": self.school.id, "name": "AB101"})
        self._import("group,subject,day,period,room\n301,MAT,1,1,A_101\n")
        self.assertEqual(self.timetable.slot_ids.room_id.name, "A_101")

    def test_an_archived_room_is_said_not_made_again(self):
        self.room_a.active = False
        wizard = self._import("group,subject,day,period,room\n301,MAT,1,1,a-101\n")
        self.assertFalse(self.timetable.slot_ids)
        self.assertIn("archived", wizard.log)

    def _big(self, text):
        return self.env["bf.school.timetable.import"].with_user(self.office).create({
            "timetable_id": self.timetable.id, "file": base64.b64encode(text.encode())})

    def test_a_file_that_is_too_big_is_refused(self):
        with self.assertRaisesRegex(UserError, "too big"):
            self._big("group,subject,day,period\n" + "301,MAT,1,1\n" * 500000).action_import()  # 6 MB

    def test_a_file_with_too_many_lines_is_refused(self):
        with self.assertRaisesRegex(UserError, "more than"):
            self._big("group,subject,day,period\n" + "1,M,1,1\n" * 21000).action_import()  # 150 KB

    def test_a_file_without_the_needed_columns_is_refused(self):
        wizard = self.env["bf.school.timetable.import"].with_user(self.office).create({
            "timetable_id": self.timetable.id, "file": base64.b64encode(b"a,b\n1,2\n")})
        with self.assertRaises(UserError):
            wizard.action_import()


@tagged("post_install", "-at_install")
class TestIsolationAndLimits(TimetableCase):
    """Company isolation and limits of the timetable, each as it was found."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.mother = new_test_user(env, login="tt_mother", name="Maman Essai", email="m.tt@example.invalid",
                                   groups="base.group_portal")
        env["bf.school.guardian.link"].create(
            {"student_id": cls.alpha.id, "guardian_id": cls.mother.partner_id.id, "relationship": "mother"})
        for day in range(1, 10):
            cls._slot(cls, day, cls.p1, cls.math301, cls.room_a)
        cls.timetable.action_generate_days()

    def _second_timetable(self, name="Session 2", start=30, end=100, period_start=10.0, publish=True):
        timetable = self.env["bf.school.timetable"].with_user(self.office).create({
            "name": name, "school_id": self.school.id, "year_id": self.year.id, "cycle_days": 9,
            "date_start": self.today + timedelta(days=start), "date_end": self.today + timedelta(days=end)})
        period = self.env["bf.school.period"].create({
            "timetable_id": timetable.id, "name": "%s period" % name, "start_time": period_start,
            "end_time": period_start + 1.0})
        for day in range(1, 10):
            self.env["bf.school.timetable.slot"].with_user(self.office).create({
                "timetable_id": timetable.id, "day_no": day, "period_id": period.id, "course_id": self.math301.id})
        timetable.action_generate_days()
        if publish:
            timetable.action_publish()
        return timetable

    # --- A4: the link follows the school the student is enrolled in

    def test_a_calendar_link_belongs_to_the_company_of_the_school(self):
        feed = self._feed()
        self.assertFalse(self.alpha.company_id, "the card of a student seldom has a company")
        self.assertEqual(feed.company_id, self.school.company_id)
        other = self.env["res.company"].create({"name": "Autre école inc."})
        stranger = new_test_user(self.env, login="tt_other_office2", groups="bf_school_core.group_school_manager",
                                 company_id=other.id, company_ids=[(6, 0, other.ids)])
        Feed = self.env["bf.school.timetable.feed"].with_user(stranger).with_company(other)
        self.assertFalse(Feed.search([]))
        with self.assertRaises(AccessError):
            feed.with_user(stranger).with_company(other).unlink()
        self.assertTrue(self.env["bf.school.timetable.feed"].with_user(self.office).search([("id", "=", feed.id)]))

    # --- B11: each date comes from the timetable in force on it

    def test_the_calendar_link_follows_a_timetable_that_replaces_another(self):
        self.timetable.action_publish()
        second = self._second_timetable()
        feed = self._feed()
        events = feed._student_events(self.today)
        timetables = {}
        for event in events:
            match = re.match(r"^\d+-(\d+)-(\d{4}-\d{2}-\d{2})@symbifox$", event["uid"])
            slot = self.env["bf.school.timetable.slot"].browse(int(match.group(1)))
            timetables.setdefault(slot.timetable_id, set()).add(date.fromisoformat(match.group(2)))
        self.assertEqual(set(timetables), {self.timetable, second})
        self.assertLess(max(timetables[self.timetable]), self.today + timedelta(days=30))
        self.assertGreaterEqual(min(timetables[second]), self.today + timedelta(days=30))
        self.assertTrue(any(d > self.today + timedelta(days=60) for d in timetables[second]),
                        "after the end of the first one the second one goes on")

    def test_the_week_of_the_portal_follows_the_timetable_in_force_each_day(self):
        self.timetable.action_publish()
        self._second_timetable(start=3, end=100)
        by_date = self.env["bf.school.timetable"]._published_by_date(
            self.school, self.today, self.today + timedelta(days=6))
        chosen = {day: t for t, days in by_date for day in days}
        self.assertEqual(chosen[self.today + timedelta(days=2)], self.timetable)
        self.assertEqual(chosen[self.today + timedelta(days=4)].name, "Session 2")

    # --- B12: the printed copy is the published timetable, never a draft

    def test_the_printed_timetable_of_a_group_is_the_published_one_not_the_latest_draft(self):
        self.timetable.action_publish()
        self._second_timetable(name="Draft next", start=61, end=120, publish=False)
        html, _kind = self.env["ir.actions.report"]._render_qweb_html(
            "bf_school_timetable.report_group_timetable", self.g301.ids)
        text = html.decode()
        self.assertIn("Period 1", text)
        self.assertNotIn("Draft next period", text)
        self.timetable.action_draft()
        html, _kind = self.env["ir.actions.report"]._render_qweb_html(
            "bf_school_timetable.report_group_timetable", self.g301.ids)
        self.assertNotIn("Period 1", html.decode(), "no published timetable: nothing of the drafts is printed")
        self.assertIn("No published timetable", html.decode())

    # --- B19

    def test_a_teacher_added_to_a_course_is_checked_against_the_classes_it_already_has(self):
        self._slot(1, self.p1, self.ccq302)  # t_pe teaches it, t_math the math of day 1: no conflict
        with self.assertRaises(ValidationError):
            self.ccq302.write({"teacher_ids": [(4, self.t_math.id)]})
        self.assertNotIn(self.t_math, self.ccq302.teacher_ids)

    def test_two_periods_of_a_day_do_not_overlap(self):
        Period = self.env["bf.school.period"]
        with self.assertRaises(ValidationError):
            Period.create({"timetable_id": self.timetable.id, "name": "1b", "start_time": 8.5, "end_time": 9.5})
        with self.assertRaises(ValidationError):
            Period.create({"timetable_id": self.timetable.id, "name": "Inside lunch", "start_time": 12.25,
                           "end_time": 12.75})
        with self.assertRaises(ValidationError):
            self.p2.start_time = 8.5
        Period.create({"timetable_id": self.timetable.id, "name": "3", "start_time": 10.5, "end_time": 11.5})

    def test_loading_the_same_file_twice_says_what_is_already_there(self):
        text = "group,subject,day,period\n301,MAT,1,2\n"
        for _round in range(2):
            wizard = self.env["bf.school.timetable.import"].with_user(self.office).create({
                "timetable_id": self.timetable.id, "file": base64.b64encode(text.encode()), "file_name": "edt.csv"})
            wizard.action_import()
        self.assertIn("already in the timetable", wizard.log)
        self.assertEqual(len(self.timetable.slot_ids.filtered(
            lambda s: s.course_id == self.math301 and s.period_id == self.p2)), 1)

    # --- C15: the address in the calendar answer

    def test_the_calendar_answer_sets_no_cookie_and_hides_the_address_from_referers(self):
        self.timetable.action_publish()
        feed = self._feed()
        response = self.url_open("/school/timetable/%s.ics" % feed.token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Referrer-Policy"), "no-referrer")
        self.assertNotIn("session_id", response.headers.get("Set-Cookie", ""))
