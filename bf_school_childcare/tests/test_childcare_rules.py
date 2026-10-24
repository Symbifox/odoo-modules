from datetime import timedelta

from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from .common import ChildcareFixture


@tagged("post_install", "-at_install")
class TestChildcareBooking(ChildcareFixture, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_childcare()

    def test_family_books(self):
        booking = self._book()
        self.assertEqual((booking.state, booking.channel, booking.booked_by_id, booking.late_notice),
                         ("booked", "portal", self.mom.partner_id, False))
        self.assertEqual(booking.school_id, self.school)

    def test_closed_day_refused(self):
        with self.assertRaisesRegex(UserError, "closed"):
            self._book(day=self.holiday)
        with self.assertRaises(UserError):
            self._book(day=self.monday + timedelta(days=5))  # Saturday: not in the calendar
        self.assertFalse(self.env["bf.school.childcare.booking"].search([("student_id", "=", self.alice.id)]),
                         "a refused booking leaves nothing behind")

    def test_period_must_run_that_day(self):
        with self.assertRaisesRegex(UserError, "does not run"):
            self._book(slot=self.full, day=self.monday)  # a class day
        with self.assertRaisesRegex(UserError, "does not run"):
            self._book(slot=self.after, day=self.pedagogical)
        self.assertEqual(self._book(slot=self.full, day=self.pedagogical).state, "booked")
        self.after.wed = False
        self.env["bf.school.calendar.day"].search([("school_id", "=", self.school.id),
                                                   ("date", "=", self.pedagogical)]).kind = "class"
        with self.assertRaisesRegex(UserError, "does not run"):
            self._book(slot=self.after, day=self.pedagogical)  # a Wednesday the period skips

    def test_full_period_refused(self):
        self._book(student=self.alice)
        self._book(student=self.eli)
        with self.assertRaisesRegex(UserError, "full"):
            self._book(student=self.bruno)
        self.assertEqual(self.after._school_taken(self.monday), 2)

    def test_registrations_take_places(self):
        self._registration(student=self.eli, lines={self.after: ["mon"]})
        self._book(student=self.alice)
        with self.assertRaisesRegex(UserError, "full"):
            self._book(student=self.bruno)
        self.assertEqual(self._book(student=self.bruno, day=self.tuesday).state, "booked",
                         "the registration covers Mondays only")

    def test_already_registered_or_booked_refused(self):
        self._registration(lines={self.after: ["mon", "tue"]})
        with self.assertRaisesRegex(UserError, "already registered"):
            self._book()
        self._book(student=self.bruno)
        with self.assertRaisesRegex(UserError, "already booked"):
            self._book(student=self.bruno)

    def test_level_of_the_period(self):
        with self.assertRaisesRegex(UserError, "level"):
            self._book(slot=self.after_k)
        self.assertEqual(self._book(student=self.bruno, slot=self.after_k).state, "booked")

    def test_secondary_student_refused_s62_1(self):
        Link = self.env["bf.school.guardian.link"]
        Link.create({"student_id": self.chloe.id, "guardian_id": self.mom.partner_id.id, "relationship": "mother"})
        with self.assertRaisesRegex(UserError, "preschool and elementary"):
            self._book(student=self.chloe)
        with self.assertRaisesRegex(ValidationError, "preschool and elementary"):
            self._registration(student=self.chloe, activate=False)
        left = self.env["bf.school.enrollment"].search([("student_id", "=", self.eli.id)])
        left.action_leave()
        with self.assertRaisesRegex(UserError, "preschool and elementary"):
            self._book(student=self.eli)

    def test_notice_for_families_late_by_office(self):
        # The evening before at 20:00 on the school's clock: 11 hours before the morning period.
        with freeze_time(self._at(self.monday - timedelta(days=1), 20.0)):
            with self.assertRaisesRegex(UserError, "hours ahead"):
                self._book(slot=self.before)
            booking = self._office_book(slot=self.before)
        self.assertTrue(booking.late_notice)
        self.assertEqual((booking.channel, booking.booked_by_id), ("office", self.manager.partner_id))
        self.assertFalse(self._office_book(student=self.bruno).late_notice)

    def test_past_date_refused(self):
        with freeze_time(self._at(self.tuesday, 9.0)):
            with self.assertRaisesRegex(UserError, "past"):
                self._office_book(day=self.monday)

    def test_cancel_free_then_late(self):
        free = self._book()
        free._school_cancel_by(self.mom.partner_id)
        self.assertEqual((free.state, free.cancel_late, free.cancelled_by_id), ("cancelled", False, self.mom.partner_id))
        late = self._book()
        with freeze_time(self._at(self.monday, 13.0)):
            late._school_cancel_by(self.dad.partner_id)
        self.assertTrue(late.cancel_late, "less than 24 hours before: the place stays due")
        started = self._book(student=self.eli)
        with freeze_time(self._at(self.monday, 16.0)), self.assertRaisesRegex(UserError, "started"):
            started._school_cancel_by(self.mom.partner_id)
        with self.assertRaises(AccessError):
            started._school_cancel_by(self.other.partner_id)

    def test_closing_the_day_closes_bookings(self):
        booking = self._book()
        day = self.env["bf.school.calendar.day"].search([("school_id", "=", self.school.id), ("date", "=", self.monday)])
        day.with_user(self.office).write({"kind": "closure"})
        self.assertEqual((booking.state, booking.cancel_late), ("closed", False))

    def test_only_authority_books(self):
        with self.assertRaises(AccessError):
            self._book(parent=self.grandma)
        with self.assertRaises(AccessError):
            self._book(student=self.dina)


@tagged("post_install", "-at_install")
class TestChildcareRegistration(ChildcareFixture, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_childcare()

    def test_activation_checks_places_on_every_date(self):
        self._book(student=self.alice)
        self._book(student=self.eli)
        registration = self._registration(student=self.bruno, lines={self.after: ["mon"]}, activate=False)
        self.assertEqual(registration.periods_per_week, 1)
        with self.assertRaisesRegex(UserError, "full"):
            registration.action_activate()
        self.assertEqual(registration.state, "draft")
        registration.write({"line_ids": [(1, registration.line_ids.id, {"mon": False, "tue": True})]})
        registration.action_activate()
        self.assertEqual((registration.state, registration.confirmed_by_id), ("active", self.manager))
        mails = self._mails(self.mom.partner_id, registration._name) | self._mails(self.dad.partner_id, registration._name)
        self.assertEqual(len(mails), 2, "every adult with parental authority is told")
        self.assertFalse(self._mails(self.grandma.partner_id, registration._name))

    def test_activation_absorbs_the_child_s_bookings(self):
        booking = self._book()
        registration = self._registration(lines={self.after: ["mon"]})
        self.assertEqual((registration.state, booking.state, booking.cancel_late), ("active", "cancelled", False))
        self.assertEqual(self.after._school_taken(self.monday), 1)

    def test_one_registration_in_force(self):
        self._registration()
        with self.assertRaisesRegex(ValidationError, "already has"):
            self._registration(activate=False)

    def test_in_force_is_frozen(self):
        registration = self._registration(lines={self.after: ["mon"]})
        with self.assertRaises(UserError):
            registration.write({"date_end": self.year.date_end - timedelta(days=1)})
        with self.assertRaises(UserError):
            registration.line_ids.write({"tue": True})
        with self.assertRaises(UserError):
            self.env["bf.school.childcare.registration.line"].with_user(self.manager).create(
                {"registration_id": registration.id, "slot_id": self.before.id, "mon": True})
        with self.assertRaises(UserError):
            registration.unlink()
        registration.action_end()
        self.assertEqual(registration.state, "cancelled" if self.today < registration.date_start else "ended")

    def test_only_the_manager_registers(self):
        for user in (self.educator, self.teacher):
            with self.assertRaises(AccessError):
                self._registration(user=user, activate=False)
        registration = self._registration(user=self.office)
        self.assertEqual(registration.state, "active")

    def test_family_request(self):
        Registration = self.env["bf.school.childcare.registration"]
        registration = Registration._school_request_by(self.mom.partner_id, self.alice, self.monday,
                                                       {self.after: ["mon", "thu", "sat"], self.before: []})
        self.assertEqual((registration.state, registration.channel, registration.requested_by_id),
                         ("draft", "portal", self.mom.partner_id))
        self.assertEqual(registration.periods_per_week, 2)
        self.assertEqual(registration.activity_ids.user_id, self.manager)
        with self.assertRaises(UserError):
            Registration._school_request_by(self.grandma.partner_id, self.alice, self.monday, {self.after: ["mon"]})
        with self.assertRaises(UserError):
            Registration._school_request_by(self.mom.partner_id, self.bruno, self.today - timedelta(days=1),
                                            {self.after: ["mon"]})


@tagged("post_install", "-at_install")
class TestChildcareGuards(ChildcareFixture, TransactionCase):
    """🔴 Reserved fields forged by a user who is not the system: refused at creation AND at
    writing. The defaults the web client sends with a new record pass."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_childcare()

    def test_registration(self):
        Registration = self.env["bf.school.childcare.registration"].with_user(self.manager)
        base = {"student_id": self.alice.id, "school_id": self.school.id, "year_id": self.year.id,
                "date_start": self.today, "date_end": self.year.date_end,
                "line_ids": [(0, 0, {"slot_id": self.after.id, "mon": True})]}
        for forged in ({"state": "active"}, {"requested_by_id": self.mom.partner_id.id}, {"channel": "portal"},
                       {"confirmed_by_id": self.manager.id}):
            with self.assertRaisesRegex(UserError, "not by hand"):
                Registration.create(dict(base, **forged))
        registration = Registration.create(dict(base, state="draft", channel="office", requested_by_id=False))
        self.assertEqual(registration.state, "draft")
        for forged in ({"state": "active"}, {"confirmed_on": "2026-01-01 00:00:00"}):
            with self.assertRaisesRegex(UserError, "not by hand"):
                registration.write(forged)

    def test_booking(self):
        Booking = self.env["bf.school.childcare.booking"].with_user(self.manager)
        base = {"student_id": self.alice.id, "slot_id": self.after.id, "date": self.monday}
        for forged in ({"state": "cancelled"}, {"late_notice": True}, {"cancel_late": True}, {"channel": "portal"},
                       {"booked_by_id": self.mom.partner_id.id}):
            with self.assertRaisesRegex(UserError, "not by hand"):
                Booking.create(dict(base, **forged))
        booking = Booking.create(dict(base, state="booked", late_notice=False, channel="office"))
        self.assertEqual(booking.booked_by_id, self.manager.partner_id)
        for forged in ({"state": "cancelled"}, {"cancel_late": True}):
            with self.assertRaisesRegex(UserError, "not by hand"):
                booking.write(forged)
        with self.assertRaises(UserError):
            booking.write({"date": self.tuesday})

    def test_activity_absence(self):
        with self.assertRaisesRegex(UserError, "not by hand"):
            self.env["bf.school.childcare.activity"].with_user(self.manager).create(
                {"name": "Sortie", "slot_id": self.full.id, "date": self.pedagogical, "state": "confirmed"})
        Absence = self.env["bf.school.childcare.absence"].with_user(self.manager)
        with self.assertRaisesRegex(UserError, "not by hand"):
            Absence.create({"student_id": self.alice.id, "school_id": self.school.id, "date": self.monday,
                            "declared_by_id": self.mom.partner_id.id})
        absence = Absence.create({"student_id": self.alice.id, "school_id": self.school.id, "date": self.monday})
        self.assertEqual((absence.declared_by_id, absence.channel), (self.manager.partner_id, "office"))

    def test_pickup_person(self):
        Person = self.env["bf.school.childcare.pickup.person"].with_user(self.manager)
        base = {"student_id": self.alice.id, "name": "Tante Lucie", "date_from": self.monday}
        for forged in ({"state": "revoked"}, {"id_checked_on": "2026-01-01 00:00:00"},
                       {"added_by_id": self.mom.partner_id.id}, {"channel": "portal"}):
            with self.assertRaisesRegex(UserError, "not by hand"):
                Person.create(dict(base, **forged))
        person = Person.create(dict(base, state="active"))
        for forged in ({"id_checked_on": "2026-01-01 00:00:00"}, {"state": "revoked"}):
            with self.assertRaisesRegex(UserError, "not by hand"):
                person.write(forged)
        with self.assertRaises(UserError):
            person.write({"name": "Quelqu'un d'autre"})
        person.write({"phone": "819 555-0101"})

    def test_walk_alone(self):
        Walk = self.env["bf.school.childcare.walk_alone"].with_user(self.manager)
        base = {"student_id": self.alice.id, "from_time": 16.0, "date_from": self.monday, "mon": True}
        for forged in ({"state": "active"}, {"signed_by_id": self.mom.partner_id.id}, {"text_hash": "0" * 64},
                       {"channel": "paper"}):
            with self.assertRaisesRegex(UserError, "not by hand"):
                Walk.create(dict(base, **forged))
        walk = Walk.create(dict(base, state="draft"))
        with self.assertRaisesRegex(UserError, "not by hand"):
            walk.write({"state": "active"})

    def test_sheet_and_line(self):
        with freeze_time(self._at(self.monday, 15.6)):
            Sheet = self.env["bf.school.childcare.sheet"].with_user(self.educator)
            with self.assertRaisesRegex(UserError, "not by hand"):
                Sheet.create({"slot_id": self.after.id, "date": self.monday, "state": "closed"})
            with self.assertRaisesRegex(UserError, "not by hand"):
                Sheet.create({"slot_id": self.after.id, "date": self.monday, "user_id": self.manager.id})
            sheet = Sheet.create({"slot_id": self.after.id, "date": self.monday, "state": "open",
                                  "user_id": self.educator.id})
            self.assertEqual(sheet.user_id, self.educator)
            with self.assertRaisesRegex(UserError, "not by hand"):
                sheet.write({"state": "closed"})
            Line = self.env["bf.school.childcare.sheet.line"].with_user(self.educator)
            for forged in ({"presence": "absent"}, {"arrived_on": "2026-01-01 00:00:00"}, {"origin": "booking"},
                           {"late_minutes": 30}, {"departure_kind": "guardian"}):
                with self.assertRaisesRegex(UserError, "not by hand"):
                    Line.create(dict({"sheet_id": sheet.id, "student_id": self.dina.id}, **forged))
            line = Line.create({"sheet_id": sheet.id, "student_id": self.dina.id, "presence": "expected",
                                "origin": "walk_in", "late_minutes": 0})
            for forged in ({"departed_on": "2026-01-01 00:00:00"}, {"late_minutes": 0}, {"presence": "absent"},
                           {"departure_guardian_id": self.other.partner_id.id}):
                with self.assertRaisesRegex(UserError, "not by hand"):
                    line.write(forged)

    def test_slot_cannot_change_school_once_booked(self):
        self._book()
        other = self.env["bf.school"].create({"name": "Autre école"})
        with self.assertRaises(UserError):
            self.after.with_user(self.manager).write({"school_id": other.id})


@tagged("post_install", "-at_install")
class TestChildcareActivityAndPermissions(ChildcareFixture, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_childcare()

    def test_activity_minimum_places_and_cancellation(self):
        activity = self.env["bf.school.childcare.activity"].with_user(self.manager).create({
            "name": "Cabane à sucre", "slot_id": self.full.id, "date": self.pedagogical, "places": 1,
            "min_registrations": 2, "deadline": self.pedagogical - timedelta(days=3)})
        booking = self._book(slot=self.full, day=self.pedagogical)
        self.assertEqual(booking.activity_id, activity)
        with self.assertRaisesRegex(UserError, "full"):
            self._book(student=self.eli, slot=self.full, day=self.pedagogical)
        with self.assertRaisesRegex(UserError, "needed"):
            activity.action_confirm()
        with freeze_time(self._at(self.pedagogical - timedelta(days=2), 9.0)):
            with self.assertRaisesRegex(UserError, "closed"):
                self._book(student=self.bruno, slot=self.full, day=self.pedagogical)
        activity.with_user(self.manager).action_cancel()
        self.assertEqual((activity.state, booking.state, booking.cancel_late), ("cancelled", "cancelled", False))
        self.assertTrue(self._mails(self.mom.partner_id, booking._name))
        with self.assertRaises(AccessError):
            activity.with_user(self.educator).action_confirm()

    def test_person_validity_and_revocation(self):
        Person = self.env["bf.school.childcare.pickup.person"]
        person = Person._school_add_by(self.mom.partner_id, self.alice, "Tante Lucie", "tante", "", self.monday,
                                       self.tuesday)
        self.assertEqual((person.channel, person.added_by_id), ("portal", self.mom.partner_id))
        self.assertTrue(person._is_valid_on(self.monday))
        self.assertFalse(person._is_valid_on(self.pedagogical))
        self.assertFalse(person._is_valid_on(self.monday - timedelta(days=1)))
        self.assertEqual(len(self._mails(self.dad.partner_id, Person._name)), 1, "the other parent is told")
        with self.assertRaises(AccessError):
            Person._school_add_by(self.grandma.partner_id, self.alice, "X", "", "", self.monday)
        with self.assertRaises(UserError):
            Person._school_add_by(self.mom.partner_id, self.alice, "X", "", "", self.today - timedelta(days=1))
        with self.assertRaises(AccessError):
            person._school_remove_by(self.other.partner_id)
        person._school_remove_by(self.dad.partner_id)
        self.assertEqual((person.state, person.revoked_by_id), ("revoked", self.dad.partner_id))
        self.assertFalse(person._is_valid_on(self.monday))

    def test_walk_alone_signed_age_and_revoked(self):
        Walk = self.env["bf.school.childcare.walk_alone"]
        self.school.childcare_walk_alone_min_age = 10
        with self.assertRaisesRegex(ValidationError, "10"):
            Walk._school_sign_by(self.mom.partner_id, self.alice, ["mon"], 16.0, self.monday)
        self.assertFalse(Walk.search([("student_id", "=", self.alice.id)]), "nothing left behind")
        self.school.childcare_walk_alone_min_age = 8
        walk = Walk._school_sign_by(self.mom.partner_id, self.alice, ["mon", "wed"], 16.0, self.monday)
        self.assertEqual((walk.state, walk.signed_by_id, walk.channel), ("active", self.mom.partner_id, "portal"))
        self.assertEqual(walk.text_hash, walk._school_fingerprint(walk.text_signed))
        self.assertEqual(len(walk.text_hash), 64)
        with self.assertRaises(AccessError):
            Walk._school_sign_by(self.grandma.partner_id, self.alice, ["mon"], 16.0, self.monday)
        with self.assertRaises(UserError):
            walk.with_user(self.manager).write({"from_time": 15.0})
        walk._school_revoke_by(self.dad.partner_id)
        self.assertEqual((walk.state, walk.revoked_by_id), ("revoked", self.dad.partner_id))
        self.assertEqual(len(self._mails(self.mom.partner_id, Walk._name)), 2, "signed, then revoked")

    def test_walk_alone_on_paper(self):
        walk = self.env["bf.school.childcare.walk_alone"].with_user(self.manager).create(
            {"student_id": self.alice.id, "from_time": 16.0, "date_from": self.monday, "mon": True})
        self.assertEqual(walk.signer_candidate_ids, self.mom.partner_id | self.dad.partner_id)
        walk.paper_signer_id = self.grandma.partner_id
        with self.assertRaises(AccessError):
            walk.action_record_paper()
        walk.paper_signer_id = self.dad.partner_id
        walk.action_record_paper()
        self.assertEqual((walk.state, walk.channel, walk.signed_by_id), ("active", "paper", self.dad.partner_id))
