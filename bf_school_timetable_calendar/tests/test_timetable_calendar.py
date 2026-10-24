from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.bf_school_timetable_calendar.models.timetable import _seed_calendar_from_timetables


@tagged("post_install", "-at_install")
class TestTimetableCalendar(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.today = fields.Date.context_today(env["res.partner"])
        # A Monday three weeks ahead: the dates of the cycle do not depend on the day the tests run.
        cls.monday = cls.today + timedelta(days=21 - cls.today.weekday())
        cls.school = env["bf.school"].create({"name": "École du Pont"})
        cls.year = env["bf.school.year"].create({
            "name": "2026-2027", "school_id": cls.school.id,
            "date_start": cls.today - timedelta(days=60), "date_end": cls.today + timedelta(days=200)})
        cls.office = new_test_user(env, login="ttc_office", groups="bf_school_core.group_school_manager")
        cls.Calendar = env["bf.school.calendar.day"].with_user(cls.office)
        cls.Timetable = env["bf.school.timetable"].with_user(cls.office)

    def _timetable(self, start=None, end=None, name="Horaire"):
        return self.Timetable.create({
            "name": name, "school_id": self.school.id, "year_id": self.year.id, "cycle_days": 5,
            "date_start": start or self.monday, "date_end": end or self.monday + timedelta(days=13)})

    def _calendar(self):
        self.year.with_user(self.office).action_school_generate_calendar()

    def _cal(self, day):
        return self.Calendar.search([("school_id", "=", self.school.id), ("date", "=", day)])

    def _day(self, timetable, day):
        return timetable.day_ids.filtered(lambda d: d.date == day)

    def _cycle(self, timetable):
        return [(d.date, d.kind, d.cycle_day) for d in timetable.day_ids.sorted("date")]

    def test_without_a_calendar_the_timetable_is_as_before(self):
        timetable = self._timetable()
        self.assertFalse(timetable.day_ids, "no calendar: the button makes the days")
        self.assertFalse(timetable.calendar_kept)
        timetable.action_generate_days()
        self.assertEqual(len(timetable.day_ids), 10)
        day = self._day(timetable, self.monday)
        day.with_user(self.office).write({"kind": "pedagogical"})
        self.assertEqual(day.cycle_day, 0)

    def test_the_calendar_gives_the_days(self):
        self._calendar()
        self._cal(self.monday + timedelta(days=2)).write({"kind": "pedagogical"})
        self._cal(self.monday + timedelta(days=7)).write({"kind": "break"})
        timetable = self._timetable()
        self.assertTrue(timetable.calendar_kept)
        self.assertEqual(len(timetable.day_ids), 10, "the weekdays of the calendar between the dates")
        cycle = self._cycle(timetable)
        self.assertEqual([c[1] for c in cycle][:8], ["class", "class", "pedagogical", "class", "class",
                                                     "holiday", "class", "class"])
        self.assertEqual([c[2] for c in cycle][:9], [1, 2, 0, 3, 4, 0, 5, 1, 2])
        # Pressed again, nothing changes.
        timetable.action_generate_days()
        self.assertEqual(self._cycle(timetable), cycle)
        # The button reads the calendar, never the weekdays: a weekday the calendar does not hold stays out.
        tuesday = self.monday + timedelta(days=1)
        self._cal(tuesday).unlink()
        timetable.action_generate_days()
        self.assertFalse(self._day(timetable, tuesday))

    def test_a_change_of_the_calendar_follows(self):
        self._calendar()
        timetable = self._timetable()
        tuesday = self.monday + timedelta(days=1)
        self._cal(tuesday).write({"kind": "holiday"})
        self.assertEqual((self._day(timetable, tuesday).kind, self._day(timetable, tuesday).cycle_day), ("holiday", 0))
        self.assertEqual(self._day(timetable, tuesday + timedelta(days=1)).cycle_day, 2, "the cycle numbered again")
        self._cal(tuesday).write({"kind": "class"})
        self.assertEqual(self._day(timetable, tuesday).cycle_day, 2)
        # A day removed from the calendar leaves the timetable; one added comes in.
        self._cal(tuesday).unlink()
        self.assertFalse(self._day(timetable, tuesday))
        saturday = self.monday + timedelta(days=5)
        self.Calendar.create({"school_id": self.school.id, "date": saturday, "kind": "class"})
        self.assertEqual(self._day(timetable, saturday).kind, "class")
        # A day moved to another date moves in the timetable.
        sunday = saturday + timedelta(days=1)
        self._cal(saturday).write({"date": sunday})
        self.assertFalse(self._day(timetable, saturday))
        self.assertTrue(self._day(timetable, sunday))

    def test_a_day_moved_to_another_timetable_leaves_the_first(self):
        self._calendar()
        first = self._timetable(end=self.monday + timedelta(days=4))
        second = self._timetable(start=self.monday + timedelta(days=7), end=self.monday + timedelta(days=13),
                                 name="Semaine 2")
        tuesday, saturday = self.monday + timedelta(days=1), self.monday + timedelta(days=12)
        self._cal(tuesday).write({"date": saturday})
        self.assertFalse(self._day(first, tuesday), "the old date leaves the timetable it was in")
        self.assertEqual(self._day(second, saturday).kind, "class")

    def test_an_exam_day_stays_on_a_class_day(self):
        self._calendar()
        timetable = self._timetable()
        day = self._day(timetable, self.monday).with_user(self.office)
        day.write({"kind": "exam", "note": "Examen de fin d'étape"})
        self.assertEqual((day.kind, day.cycle_day), ("exam", 1))
        self._cal(self.monday).write({"note": "Examens", "kind": "class"})
        self.assertEqual(day.kind, "exam", "the calendar still says class: the exam stays")
        self._cal(self.monday).write({"kind": "closure"})
        self.assertEqual((day.kind, day.cycle_day), ("holiday", 0), "a closure ends the exam")
        self._cal(self.monday).write({"kind": "class"})
        self.assertEqual(day.kind, "class")
        day.write({"kind": "exam"})
        day.write({"kind": "class"})
        self.assertEqual(day.kind, "class")

    def test_the_timetable_does_not_change_the_calendar_days(self):
        self._calendar()
        timetable = self._timetable()
        day = self._day(timetable, self.monday).with_user(self.office)
        Day = self.env["bf.school.timetable.day"].with_user(self.office)
        for vals in ({"kind": "pedagogical"}, {"kind": "holiday"}, {"date": self.monday + timedelta(days=5)},
                     {"timetable_id": self._timetable(name="Autre").id}):
            with self.assertRaisesRegex(UserError, "school calendar", msg=str(vals)):
                day.write(vals)
        with self.assertRaisesRegex(UserError, "school calendar"):
            Day.create({"timetable_id": timetable.id, "date": self.monday + timedelta(days=5), "kind": "class"})
        with self.assertRaisesRegex(UserError, "school calendar"):
            day.unlink()
        self._cal(self.monday).write({"kind": "pedagogical"})
        with self.assertRaisesRegex(UserError, "school calendar"):
            day.write({"kind": "exam"})
        self.assertEqual(day.kind, "pedagogical")

    def test_new_dates_of_the_timetable_read_the_calendar(self):
        self._calendar()
        timetable = self._timetable()
        timetable.write({"date_end": self.monday + timedelta(days=20)})
        self.assertEqual(len(timetable.day_ids), 15)
        timetable.write({"date_start": self.monday + timedelta(days=7)})
        self.assertEqual(min(timetable.day_ids.mapped("date")), self.monday + timedelta(days=7))

    def test_a_timetable_of_another_school_is_left_alone(self):
        other = self.env["bf.school"].create({"name": "Autre école"})
        year = self.env["bf.school.year"].create({"name": "2026-2027", "school_id": other.id,
                                                  "date_start": self.year.date_start, "date_end": self.year.date_end})
        timetable = self.Timetable.create({"name": "Autre", "school_id": other.id, "year_id": year.id,
                                           "cycle_days": 5, "date_start": self.monday,
                                           "date_end": self.monday + timedelta(days=13)})
        timetable.action_generate_days()
        self._calendar()
        self._cal(self.monday).write({"kind": "holiday"})
        self.assertEqual(self._day(timetable, self.monday).kind, "class")

    def test_the_first_days_of_a_year_take_what_the_timetable_holds(self):
        """🔴 Generated as class days, the calendar turned the timetable's pedagogical days into class."""
        timetable = self._timetable()
        timetable.action_generate_days()
        wednesday, thursday = self.monday + timedelta(days=2), self.monday + timedelta(days=3)
        self._day(timetable, wednesday).write({"kind": "pedagogical"})
        self._day(timetable, thursday).write({"kind": "exam"})
        timetable.sudo().state = "published"
        cycle = self._cycle(timetable)
        self._calendar()
        self.assertTrue(timetable.calendar_kept)
        self.assertEqual(self._cal(wednesday).kind, "pedagogical", "the calendar takes the timetable's day")
        self.assertEqual(self._cal(thursday).kind, "class", "an exam day is a class day of the calendar")
        self.assertEqual(self._cycle(timetable), cycle, "the published cycle is unchanged")

    def test_the_seeding_key_is_the_systems(self):
        """A caller who sends the seeding key does not stop the timetable from following the calendar."""
        self._calendar()
        timetable = self._timetable()
        self._cal(self.monday).with_context(school_calendar_seeding=True).write({"kind": "holiday"})
        self.assertEqual(self._day(timetable, self.monday).kind, "holiday")

    def test_the_seeding_key_does_not_skip_the_first_days(self):
        """🔴 Sent to the calendar's own button, the key does not reach its sudo."""
        timetable = self._timetable()
        timetable.action_generate_days()
        wednesday = self.monday + timedelta(days=2)
        self._day(timetable, wednesday).write({"kind": "pedagogical"})
        self.year.with_user(self.office).with_context(school_calendar_seeding=True).action_school_generate_calendar()
        self.assertEqual(self._cal(wednesday).kind, "pedagogical")
        self.assertEqual(self._day(timetable, wednesday).kind, "pedagogical")

    def test_install_aligns_a_timetable_of_a_year_already_kept(self):
        self._calendar()
        timetable = self._timetable()
        self._day(timetable, self.monday).sudo().with_context(skip_renumber=True).write({"kind": "holiday"})
        _seed_calendar_from_timetables(self.env)
        self.assertEqual(self._day(timetable, self.monday).kind, "class")
        self.assertEqual(self._cal(self.monday).kind, "class", "a year already kept is not filled again")

    def test_install_keeps_the_days_of_a_timetable(self):
        # A draft made first, then the published timetable: the published one is read first.
        draft = self._timetable(name="Brouillon", end=self.year.date_end + timedelta(days=10))
        draft.action_generate_days()
        after = self.year.date_end + timedelta(days=1)
        while after.weekday() >= 5:
            after += timedelta(days=1)
        self.assertTrue(self._day(draft, after), "the draft runs past the end of the school year")
        published = self._timetable()
        published.action_generate_days()
        self._day(published, self.monday).write({"kind": "exam"})
        self._day(published, self.monday + timedelta(days=2)).write({"kind": "pedagogical"})
        self._day(published, self.monday + timedelta(days=3)).write({"kind": "holiday"})
        published.sudo().state = "published"
        self.assertFalse(published.calendar_kept)
        _seed_calendar_from_timetables(self.env)
        self.assertEqual(self._cal(self.monday).kind, "class", "an exam day is a class day of the calendar")
        self.assertEqual(self._cal(self.monday + timedelta(days=2)).kind, "pedagogical")
        self.assertEqual(self._cal(self.monday + timedelta(days=3)).kind, "holiday")
        self.assertFalse(self._cal(after), "no school year covers that date")
        self.assertEqual(self._day(published, self.monday).kind, "exam", "the exam stays in the timetable")
        self.assertEqual(self._day(draft, self.monday + timedelta(days=2)).kind, "pedagogical",
                         "the draft follows the calendar taken from the published timetable")
        count = self.Calendar.search_count([("school_id", "=", self.school.id)])
        _seed_calendar_from_timetables(self.env)
        self.assertEqual(self.Calendar.search_count([("school_id", "=", self.school.id)]), count,
                         "a school that keeps its calendar is not filled again")
