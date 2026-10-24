import hashlib
from datetime import date, timedelta

from freezegun import freeze_time

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from .common import ChildcareFixture


@tagged("post_install", "-at_install")
class TestChildcareEdges(ChildcareFixture, TransactionCase):
    """Edge cases: the age to leave alone, the company of the sensitive records, the day of a
    sheet, a child added by mistake, a calendar or a period that changes under the bookings."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_childcare()
        cls.after.capacity = 0
        cls.Walk = cls.env["bf.school.childcare.walk_alone"]
        cls.Person = cls.env["bf.school.childcare.pickup.person"]
        cls.Restriction = cls.env["bf.school.childcare.restriction"]
        # Old enough for a minimum of 12; Alice (9 or 10) is not.
        cls.eli.student_birthdate = date(cls.today.year - 13, 3, 1)

    def _arrive(self, student, day, hour=15.6, slot=None):
        sheet = self._sheet(slot=slot, day=day)
        line = self._line(sheet, student).with_user(self.educator)
        with freeze_time(self._at(day, hour)):
            line.action_arrive()
        return line

    # --- Leaving alone: the school's minimum age ---------------------------------------------

    def test_minimum_age_on_the_portal_also_after_the_school_year(self):
        self.school.childcare_walk_alone_min_age = 12
        with self.assertRaisesRegex(ValidationError, "12"):
            self.Walk._school_sign_by(self.mom.partner_id, self.alice, ["mon"], 16.0, self.monday)
        # No enrolment covers the day after the year: the school of today decides, never "no minimum".
        with self.assertRaisesRegex(UserError, "school year"):
            self.Walk._school_sign_by(self.mom.partner_id, self.alice, ["mon"], 16.0,
                                      self.year.date_end + timedelta(days=1))
        self.assertFalse(self.Walk.search([("student_id", "=", self.alice.id)]))

    def test_minimum_age_when_the_office_changes_the_student(self):
        self.school.childcare_walk_alone_min_age = 12
        walk = self.Walk.with_user(self.manager).create({
            "student_id": self.eli.id, "from_time": 16.0, "date_from": self.monday, "mon": True})
        with self.assertRaisesRegex(ValidationError, "12"):
            walk.write({"student_id": self.alice.id})
        # A date no enrolment covers: the school of today still sets the minimum.
        with self.assertRaisesRegex(ValidationError, "12"):
            self.Walk.with_user(self.manager).create({
                "student_id": self.alice.id, "from_time": 16.0, "mon": True,
                "date_from": self.year.date_end + timedelta(days=1)})

    def test_minimum_age_raised_after_signing_refuses_the_departure(self):
        self.school.childcare_walk_alone_min_age = 12
        self._book(student=self.eli)
        walk = self.Walk._school_sign_by(self.mom.partner_id, self.eli, ["mon"], 15.5, self.monday)
        self.assertEqual(walk.state, "active")
        line = self._arrive(self.eli, self.monday)
        self.school.childcare_walk_alone_min_age = 14
        with freeze_time(self._at(self.monday, 16.5)):
            self.assertNotIn("walk_alone", [o["kind"] for o in line._school_permitted_options()])
            with self.assertRaisesRegex(UserError, "leave alone"):
                line._school_depart("walk_alone")
        self.school.childcare_walk_alone_min_age = 12
        with freeze_time(self._at(self.monday, 16.5)):
            line._school_depart("walk_alone")
        self.assertEqual(line.departure_kind, "walk_alone")

    def test_the_text_accepted_is_kept_and_fingerprinted(self):
        walk = self.Walk._school_sign_by(self.mom.partner_id, self.eli, ["mon"], 15.5, self.monday)
        self.assertIn("Éli Garde", walk.text_signed)
        self.assertIn("responsabilité", walk.text_signed, "the clause, in the signer's language")
        self.assertEqual(walk.text_hash, hashlib.sha256(walk.text_signed.encode("utf-8")).hexdigest())
        with self.assertRaises(UserError):
            walk.with_user(self.manager).write({"text_signed": "Autre chose"})

    # --- Company of the sensitive records -----------------------------------------------------

    def test_restriction_follows_the_school_company_not_the_card(self):
        self.assertFalse(self.alice.company_id, "the core leaves the student card without company")
        restriction = self.Restriction.with_user(self.manager).create({"student_id": self.alice.id,
                                                                       "name": "Oncle Interdit",
                                                                       "detail": "Ordonnance"})
        self.assertEqual(restriction.company_id, self.school.company_id)
        other = self.env["res.company"].create({"name": "Autre école inc."})
        manager_b = new_test_user(self.env, login="cc_manager_b", company_id=other.id,
                                  company_ids=[(6, 0, other.ids)],
                                  groups="bf_school_childcare.group_school_childcare_manager")
        for model in ("bf.school.childcare.restriction", "bf.school.childcare.pickup.person",
                      "bf.school.childcare.walk_alone"):
            self.assertFalse(self.env[model].with_user(manager_b).search([("student_id", "=", self.alice.id)]),
                             model)
        self.Person._school_add_by(self.mom.partner_id, self.alice, "Voisine", "voisine", "", self.today)
        self.assertFalse(self.Person.with_user(manager_b).search([]))

    # --- The day of a sheet -------------------------------------------------------------------

    def test_future_sheet_takes_no_arrival(self):
        sheet = self._sheet()  # opened on Monday, as on its day
        self._book(student=self.eli)
        with freeze_time(self._at(self.monday, 12.0)):
            sheet.with_user(self.educator).action_refresh()
        line = self._line(sheet, self.eli).with_user(self.educator)
        with freeze_time(self._at(self.monday - timedelta(days=3), 15.6)), \
                self.assertRaisesRegex(UserError, "later date"):
            line.action_arrive()
        with freeze_time(self._at(self.today, 12.0)), self.assertRaisesRegex(UserError, "today's sheets"):
            self.env["bf.school.childcare.sheet"].with_user(self.educator).create(
                {"slot_id": self.after.id, "date": self.tuesday})

    def test_past_sheet_is_corrected_by_the_manager_only(self):
        self._book(student=self.eli)
        line = self._arrive(self.eli, self.monday)
        # 🔴 A restriction in force since Tuesday morning is not seen on Monday's sheet: only the
        # manager writes on it, and no lateness is counted for a departure recorded afterwards.
        with freeze_time(self._at(self.tuesday, 16.0)):
            with self.assertRaisesRegex(UserError, "past day"):
                line._school_depart("guardian", partner=self.mom.partner_id)
            with self.assertRaisesRegex(UserError, "past day"):
                line.action_departure()
            line.with_user(self.manager)._school_depart("guardian", partner=self.mom.partner_id)
        self.assertEqual((line.departure_kind, line.late_minutes), ("guardian", 0))
        self.assertIn("afterwards", line.sheet_id.message_ids[:1].body)

    # --- A child added by mistake ---------------------------------------------------------------

    def test_child_added_by_mistake_is_removed_by_the_educator(self):
        sheet = self._sheet()
        Line = self.env["bf.school.childcare.sheet.line"].with_user(self.educator)
        with freeze_time(self._at(self.monday, 15.6)):
            line = Line.create({"sheet_id": sheet.id, "student_id": self.dina.id})
            line.action_remove()
            self.assertFalse(line.exists())
            self.assertIn("Dina Autre", sheet.message_ids[:1].body)
            Line.create({"sheet_id": sheet.id, "student_id": self.dina.id})
        self._book(student=self.eli)
        with freeze_time(self._at(self.monday, 15.6)):
            sheet.with_user(self.educator).action_refresh()
            booked = self._line(sheet, self.eli).with_user(self.educator)
            with self.assertRaisesRegex(UserError, "added by mistake"):
                booked.action_remove()
            self._line(sheet, self.dina).with_user(self.educator)._school_depart(
                "guardian", partner=self.other.partner_id)

    # --- The calendar or a period changes under the bookings ----------------------------------

    def test_class_day_turned_pedagogical_closes_the_bookings(self):
        booking = self._book()
        day = self.env["bf.school.calendar.day"].with_user(self.office).search(
            [("school_id", "=", self.school.id), ("date", "=", self.monday)])
        day.kind = "pedagogical"  # child care stays open, the after-school period no longer runs
        self.assertEqual(booking.state, "closed")

    def test_period_unticked_or_archived_closes_its_bookings(self):
        monday = self._book()
        tuesday = self._book(day=self.tuesday)
        self.after.with_user(self.manager).mon = False
        self.assertEqual((monday.state, tuesday.state), ("closed", "booked"))
        self.after.with_user(self.manager).active = False
        self.assertEqual(tuesday.state, "closed")

    def test_period_in_use_stays_at_its_school(self):
        self._registration()
        school_b = self.env["bf.school"].create({"name": "Autre campus"})
        with self.assertRaisesRegex(UserError, "stays at its school"):
            self.after.with_user(self.manager).school_id = school_b

    def test_backdated_registration_keeps_past_bookings(self):
        past_monday = self.today - timedelta(days=self.today.weekday() + 14)
        with freeze_time(self._at(past_monday - timedelta(days=3), 10.0)):
            past = self._office_book(day=past_monday)
        future = self._book()
        self._registration(start=self.year.date_start)
        self.assertEqual(past.state, "booked", "the child came: a used booking is not cancelled for free")
        self.assertEqual(future.state, "cancelled")

    def test_year_shortened_under_its_calendar_is_refused(self):
        with self.assertRaisesRegex(ValidationError, "No school year"):
            self.year.with_user(self.office).date_end = self.today + timedelta(days=30)

    # --- Who attends ------------------------------------------------------------------------------

    def test_group_mixing_elementary_and_secondary_admits_no_one(self):
        mixed = self.env["bf.school.group"].create({
            "name": "Groupe 6-S1", "school_id": self.school.id, "year_id": self.year.id,
            "level_ids": [(6, 0, (self.env.ref("bf_school_core.level_e6") | self.s1).ids)]})
        kid = self.env["res.partner"].create({"name": "Félix Garde", "is_student": True})
        self.env["bf.school.enrollment"].create({"student_id": kid.id, "group_id": mixed.id})
        self.env["bf.school.guardian.link"].create({"student_id": kid.id, "guardian_id": self.mom.partner_id.id,
                                                    "relationship": "mother"})
        self.assertFalse(kid._childcare_levels(self.school, self.monday))
        with self.assertRaisesRegex(UserError, "preschool and elementary"):
            self._book(student=kid)

    # --- A pick-up restriction under another spelling ---------------------------------------------

    def test_restricted_name_inside_an_authorized_person_name(self):
        self.Restriction.with_user(self.manager).create({"student_id": self.eli.id, "name": "Gaston Fictif"})
        person = self.Person._school_add_by(self.mom.partner_id, self.eli, "M. Gaston Fictif", "voisin", "",
                                            self.today)
        activities = self.env["mail.activity"].sudo().search([("res_model", "=", person._name),
                                                              ("res_id", "=", person.id)])
        self.assertEqual(activities.user_id, self.manager, "the manager reviews, the family is not told why")
        # 🔴 Never on the student card: every staff member reads it.
        self.assertFalse(self.env["mail.activity"].sudo().search([("res_model", "=", "res.partner"),
                                                                   ("res_id", "=", self.eli.id)]))
        for user in (self.teacher, self.educator):
            self.assertFalse(self.env["mail.activity"].with_user(user).search([("res_model", "=", person._name)]),
                             user.login)
        # 🔴 Added from the portal, the adult never follows the person: the note "activity done"
        # would reach them. Only the manager follows it.
        other = self.Person.with_user(self.mom)._school_add_by(
            self.mom.partner_id, self.eli, "Gaston Fictif fils", "voisin", "", self.today)
        self.assertEqual(other.sudo().message_partner_ids, self.manager.partner_id)
        person.sudo().write({"id_checked_on": "2026-01-01 12:00:00"})
        self._book(student=self.eli)
        line = self._arrive(self.eli, self.monday)
        with freeze_time(self._at(self.monday, 17.0)):
            self.assertNotIn(person, [o["person"] for o in line._school_permitted_options() if o["person"]])
            with self.assertRaisesRegex(UserError, "may not pick up"):
                line._school_depart("authorized", person=person)

    # --- Restrictions, dates and languages -----------------------------------------------------

    def test_restricted_adult_does_not_choose_who_else_takes_the_child(self):
        self.Restriction.with_user(self.manager).create({"student_id": self.eli.id, "name": "Papa Garde",
                                                         "partner_id": self.dad.partner_id.id})
        with self.assertRaisesRegex(UserError, "Contact the school"):
            self.Person._school_add_by(self.dad.partner_id, self.eli, "Ami de papa", "ami", "", self.today)
        with self.assertRaisesRegex(UserError, "Contact the school"):
            self.Walk._school_sign_by(self.dad.partner_id, self.eli, ["mon"], 15.5, self.monday)
        # The other parent still may.
        self.Person._school_add_by(self.mom.partner_id, self.eli, "Voisine", "voisine", "", self.today)

    def test_guardian_under_a_restriction_written_inside_their_name(self):
        self.Restriction.with_user(self.manager).create({"student_id": self.eli.id, "name": "Papa"})
        self._book(student=self.eli)
        line = self._arrive(self.eli, self.monday)
        with freeze_time(self._at(self.monday, 17.0)):
            self.assertNotIn(self.dad.partner_id, [o["partner"] for o in line._school_permitted_options()])
            with self.assertRaisesRegex(UserError, "Keep the child"):
                line._school_depart("guardian", partner=self.dad.partner_id)

    def test_past_sheet_takes_no_arrival_even_from_the_manager(self):
        self._book(student=self.eli)
        self._book(student=self.alice)
        line = self._arrive(self.eli, self.monday)
        alice = self._line(line.sheet_id, self.alice)
        with freeze_time(self._at(self.tuesday, 9.0)):
            with self.assertRaisesRegex(UserError, "past day"):
                alice.with_user(self.manager).action_arrive()
            with self.assertRaisesRegex(UserError, "past day"):
                self.env["bf.school.childcare.sheet.line"].with_user(self.manager).create(
                    {"sheet_id": line.sheet_id.id, "student_id": self.dina.id})

    def test_future_sheet_does_not_close(self):
        sheet = self._sheet()
        with freeze_time(self._at(self.monday - timedelta(days=3), 12.0)), \
                self.assertRaisesRegex(UserError, "later date"):
            sheet.with_user(self.manager).action_close()

    def test_ended_registration_still_expected_today(self):
        registration = self._registration(lines={self.after: ["mon"]})
        with freeze_time(self._at(self.monday, 10.0)):
            registration.with_user(self.manager).action_end()
        self.assertEqual((registration.state, registration.date_end), ("ended", self.monday))
        sheet = self._sheet()
        self.assertEqual(self._line(sheet, self.alice).origin, "registration")

    def test_activity_planned_after_the_first_bookings(self):
        first = self._book(slot=self.full, day=self.pedagogical)
        activity = self.env["bf.school.childcare.activity"].with_user(self.manager).create({
            "name": "Cabane à sucre", "slot_id": self.full.id, "date": self.pedagogical, "min_registrations": 2})
        self.assertEqual((first.activity_id, activity.booked_count), (activity, 1))
        activity.action_cancel()
        self.assertEqual(first.state, "cancelled")

    def test_registration_days_within_the_period(self):
        self.after.with_user(self.manager).fri = False
        with self.assertRaisesRegex(ValidationError, "does not run"):
            self._registration(lines={self.after: ["mon", "fri"]})

    def test_paper_and_portal_text_in_the_language_read(self):
        walk = self.Walk._school_sign_by(self.mom.partner_id, self.eli, ["mon"], 15.5, self.monday, lang="en_US")
        self.assertIn("leave child care alone", walk.text_signed)

    def test_no_period_runs_on_a_day_closed_to_child_care(self):
        self.assertFalse(self.full._runs_on(self.holiday), "a holiday is a day without class, and closed")
        day = self.env["bf.school.calendar.day"].with_user(self.office).search(
            [("school_id", "=", self.school.id), ("date", "=", self.tuesday)])
        day.childcare_open = False  # a class day, child care closed (staff training, for example)
        self.assertFalse(self.after._runs_on(self.tuesday))
        with self.assertRaises(UserError):
            self._book(day=self.tuesday)
