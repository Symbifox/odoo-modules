from datetime import date, datetime, timedelta
from unittest.mock import patch

from freezegun import freeze_time

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestSchoolCalendar(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.company.partner_id.tz = "America/Toronto"
        cls.school = env["bf.school"].create({"name": "École du Calendrier"})
        cls.other_school = env["bf.school"].create({"name": "Autre école"})
        cls.year = env["bf.school.year"].create({
            "name": "2026-2027", "school_id": cls.school.id,
            "date_start": date(2026, 8, 31), "date_end": date(2026, 9, 11)})
        # 🔴 The Administration alone, as a school sets it up; never the superuser.
        cls.office = new_test_user(env, login="school_cal_office", groups="bf_school_core.group_school_manager")
        cls.staff = new_test_user(env, login="school_cal_staff", groups="bf_school_core.group_school_user")
        cls.Day = env["bf.school.calendar.day"]

    def _generate(self, user=None):
        return self.year.with_user(user or self.office).action_school_generate_calendar()

    def test_generate_weekdays_only_and_keeps_what_is_set(self):
        # A pedagogical day set first is not turned into a class day.
        self.Day.with_user(self.office).create({"school_id": self.school.id, "date": date(2026, 9, 4),
                                                "kind": "pedagogical"})
        action = self._generate()
        days = self.Day.search([("school_id", "=", self.school.id)])
        self.assertEqual(len(days), 10, "two weeks, Monday to Friday")
        self.assertFalse(days.filtered(lambda d: d.date.weekday() >= 5), "no weekend day")
        self.assertEqual(days.filtered(lambda d: d.date == date(2026, 9, 4)).kind, "pedagogical")
        self.assertEqual(days.year_id, self.year)
        self.assertIn("9", action["params"]["message"])
        self._generate()
        self.assertEqual(self.Day.search_count([("school_id", "=", self.school.id)]), 10, "pressed twice, nothing added")

    def test_generate_is_for_the_administration(self):
        with self.assertRaises(AccessError):
            self._generate(self.staff)
        self.assertFalse(self.Day.search_count([("school_id", "=", self.school.id)]))

    def test_staff_reads_administration_writes(self):
        day = self.Day.with_user(self.office).create({"school_id": self.school.id, "date": date(2026, 9, 1)})
        self.assertEqual(day.with_user(self.staff).kind, "class")
        with self.assertRaises(AccessError):
            day.with_user(self.staff).write({"kind": "holiday"})
        with self.assertRaises(AccessError):
            self.Day.with_user(self.staff).create({"school_id": self.school.id, "date": date(2026, 9, 2)})
        day.with_user(self.office).write({"kind": "holiday"})
        self.assertEqual(day.kind, "holiday")

    def test_childcare_open_follows_the_kind_and_can_be_set(self):
        Day = self.Day.with_user(self.office)
        kinds = {"class": True, "pedagogical": True, "break": False, "holiday": False, "closure": False}
        for offset, (kind, expected) in enumerate(kinds.items()):
            day = Day.create({"school_id": self.school.id, "date": date(2026, 8, 31) + timedelta(days=offset),
                              "kind": kind})
            self.assertEqual(day.childcare_open, expected, kind)
        spring_break = Day.create({"school_id": self.school.id, "date": date(2026, 9, 7), "kind": "break"})
        spring_break.childcare_open = True
        self.assertTrue(spring_break.childcare_open, "the office opens child care on a break")
        self.assertTrue(self.school._calendar_childcare_open(date(2026, 9, 7)))
        self.assertFalse(self.school._calendar_childcare_open(date(2026, 9, 8)),
                         "a date the calendar does not hold is closed")

    def test_one_day_per_school_and_date_within_a_year(self):
        Day = self.Day.with_user(self.office)
        Day.create({"school_id": self.school.id, "date": date(2026, 9, 1)})
        with self.assertRaises(Exception), self.cr.savepoint(), mute_logger("odoo.sql_db"):
            Day.create({"school_id": self.school.id, "date": date(2026, 9, 1), "kind": "holiday"})
        with self.assertRaises(ValidationError):
            Day.create({"school_id": self.school.id, "date": date(2026, 12, 1)})
        with self.assertRaises(ValidationError):
            Day.create({"school_id": self.other_school.id, "date": date(2026, 9, 1)})
        with self.assertRaises(ValidationError):
            Day.create({"school_id": self.school.id, "date": date(2026, 9, 2), "early_dismissal_time": 25})

    def test_closing_child_care_calls_the_hook(self):
        Day = self.Day.with_user(self.office)
        day = Day.create({"school_id": self.school.id, "date": date(2026, 9, 1)})
        other = Day.create({"school_id": self.school.id, "date": date(2026, 9, 2), "kind": "break"})
        Model = type(self.Day)
        with patch.object(Model, "_school_childcare_closed", autospec=True) as hook:
            other.write({"note": "Relâche"})
            hook.assert_not_called()
            day.write({"kind": "closure"})
            self.assertEqual(hook.call_count, 1)
            self.assertEqual(hook.call_args[0][0], day)
        with patch.object(Model, "_school_childcare_closed", autospec=True) as hook:
            day.write({"kind": "class"})
            day.unlink()
            self.assertEqual(hook.call_count, 1, "a class day removed from the calendar is closed")

    def test_today_is_the_school_s(self):
        # 🔴 22:30 in Montréal is 02:30 UTC the next day: "today" is still the 11th at school.
        with freeze_time(datetime(2026, 10, 12, 2, 30)):
            self.assertEqual(self.school._calendar_today(), date(2026, 10, 11))
            self.assertEqual(self.school._calendar_now().hour, 22)
        self.assertEqual(self.school._calendar_at(date(2026, 10, 11), 22.5), datetime(2026, 10, 12, 2, 30))
        self.assertEqual(self.school._calendar_at(date(2026, 1, 15), 15.75), datetime(2026, 1, 15, 20, 45),
                         "winter time: UTC-5")
        local = self.school._calendar_local(datetime(2026, 10, 12, 2, 30))
        self.assertEqual((local.day, local.hour, local.minute), (11, 22, 30))

    def test_display_name_and_weekday_in_french(self):
        if not self.env["res.lang"]._lang_get("fr_CA"):
            self.env["res.lang"]._activate_lang("fr_CA")
        day = self.Day.with_user(self.office).create(
            {"school_id": self.school.id, "date": date(2026, 9, 4), "kind": "pedagogical"})
        fr = day.with_context(lang="fr_CA")
        self.assertEqual(fr.weekday, "Vendredi")
        self.assertIn("Journée pédagogique", fr.display_name)
