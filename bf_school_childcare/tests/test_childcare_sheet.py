from datetime import timedelta

from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from .common import ChildcareFixture


@tagged("post_install", "-at_install")
class TestChildcareSheet(ChildcareFixture, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_childcare()
        cls.after.capacity = 0
        cls.Person = cls.env["bf.school.childcare.pickup.person"]

    def _arrived(self, student=None, sheet=None, hour=15.6):
        sheet = sheet or self._sheet()
        line = self._line(sheet, student or self.alice)
        with freeze_time(self._at(sheet.date, hour)):
            line.with_user(self.educator).action_arrive()
        return line.with_user(self.educator)

    # --- Who is expected --------------------------------------------------------------------

    def test_sheet_lists_the_expected_and_the_absent(self):
        self._registration(lines={self.after: ["mon"]})
        self._registration(student=self.bruno, lines={self.after: ["mon"]})
        self._book(student=self.eli)
        self.env["bf.school.childcare.absence"]._school_declare_by(self.mom.partner_id, self.eli, self.monday,
                                                                  self.after, "Rendez-vous")
        reason = self.env.ref("bf_school_attendance.reason_illness")
        self.env["bf.school.absence.declaration"].create({
            "student_id": self.bruno.id, "date_from": self.monday, "date_to": self.monday, "part": "full",
            "reason_id": reason.id})
        sheet = self._sheet()
        self.assertEqual(sheet.user_id, self.educator)
        lines = {line.student_id: line for line in sheet.line_ids}
        self.assertEqual(set(lines), {self.alice, self.bruno, self.eli})
        self.assertEqual((lines[self.alice].origin, lines[self.alice].presence), ("registration", "expected"))
        self.assertEqual((lines[self.eli].origin, lines[self.eli].presence), ("booking", "absent"))
        self.assertEqual(lines[self.bruno].presence, "absent", "a whole day declared to the school counts")

    def test_refresh_adds_and_touches_nothing_else(self):
        self._registration(lines={self.after: ["mon"]})
        sheet = self._sheet()
        alice = self._arrived(sheet=sheet)
        self._book(student=self.eli)
        sheet.with_user(self.educator).action_refresh()
        self.assertEqual(set(sheet.line_ids.student_id), {self.alice, self.eli})
        self.assertTrue(alice.arrived_on)
        # A notice arriving while the sheet is open marks the child at once.
        self.env["bf.school.childcare.absence"]._school_declare_by(self.mom.partner_id, self.eli, self.monday)
        self.assertEqual(self._line(sheet, self.eli).presence, "absent")
        self.assertIn("Éli Garde", sheet.message_ids[:1].body)

    def test_walk_in(self):
        sheet = self._sheet()
        Line = self.env["bf.school.childcare.sheet.line"].with_user(self.educator)
        with freeze_time(self._at(self.monday, 15.6)):
            line = Line.create({"sheet_id": sheet.id, "student_id": self.dina.id})
            self.assertEqual((line.origin, line.presence), ("walk_in", "present"))
            self.assertTrue(line.arrived_on)
            with self.assertRaisesRegex(UserError, "preschool and elementary"):
                Line.create({"sheet_id": sheet.id, "student_id": self.chloe.id})
        self._book(student=self.eli)
        with freeze_time(self._at(self.monday, 15.7)):
            line = Line.create({"sheet_id": sheet.id, "student_id": self.eli.id})
        self.assertEqual(line.origin, "booking", "expected but missing from the list: not a walk-in")

    def test_sheet_only_on_a_day_the_period_runs(self):
        with self.assertRaisesRegex(UserError, "does not run"):
            self._sheet(day=self.holiday)
        with self.assertRaisesRegex(UserError, "does not run"):
            self._sheet(slot=self.full, day=self.monday)

    # --- Departures -------------------------------------------------------------------------

    def test_departure_with_a_parent(self):
        self._registration()
        line = self._arrived()
        options = line._school_permitted_options()
        self.assertEqual({o["partner"] for o in options if o["kind"] == "guardian"},
                         {self.mom.partner_id, self.dad.partner_id, self.grandma.partner_id})
        with freeze_time(self._at(self.monday, 17.0)):
            line._school_depart("guardian", partner=self.mom.partner_id)
        self.assertEqual((line.departure_kind, line.departure_guardian_id, line.late_minutes),
                         ("guardian", self.mom.partner_id, 0))
        self.assertEqual(line.departure_label, "Maman Garde")
        with freeze_time(self._at(self.monday, 17.1)), self.assertRaisesRegex(UserError, "already recorded"):
            line._school_depart("guardian", partner=self.dad.partner_id)

    def test_departure_with_someone_not_allowed_refused(self):
        self._registration()
        link = self.env["bf.school.guardian.link"].search([("student_id", "=", self.alice.id),
                                                           ("guardian_id", "=", self.grandma.partner_id.id)])
        link.can_pickup = False
        line = self._arrived()
        with freeze_time(self._at(self.monday, 16.9)):
            with self.assertRaisesRegex(UserError, "may not pick up"):
                line._school_depart("guardian", partner=self.grandma.partner_id)
            with self.assertRaisesRegex(UserError, "may not pick up"):
                line._school_depart("guardian", partner=self.other.partner_id)
        self.assertFalse(line.departed_on)

    def test_restricted_person_refused_and_never_offered(self):
        self._registration()
        Restriction = self.env["bf.school.childcare.restriction"].with_user(self.manager)
        Restriction.create({"student_id": self.alice.id, "name": "Papa Garde", "partner_id": self.dad.partner_id.id,
                            "detail": "Ordonnance de la Cour supérieure"})
        Restriction.create({"student_id": self.alice.id, "name": "Oncle Interdit"})
        line = self._arrived()
        self.assertTrue(line.has_restriction)
        self.assertIn("Papa Garde", line.restriction_names)
        self.assertNotIn("Papa Garde", line.permitted_summary)
        self.assertNotIn(self.dad.partner_id, [o["partner"] for o in line._school_permitted_options()])
        with freeze_time(self._at(self.monday, 16.9)):
            with self.assertRaisesRegex(UserError, "Keep the child"):
                line._school_depart("guardian", partner=self.dad.partner_id)
            with self.assertRaisesRegex(UserError, "Keep the child"):
                line._school_depart("other", note="L'oncle interdit, avec l'accord de la mère")
            # An authorized person under the restricted name, added by a family, is refused too.
            person = self.Person._school_add_by(self.mom.partner_id, self.alice, "Oncle  INTERDIT", "oncle", "",
                                                self.monday)
            self.assertFalse([o for o in line._school_permitted_options() if o["kind"] == "authorized"])
            with self.assertRaisesRegex(UserError, "Keep the child"):
                line._school_depart("authorized", person=person, id_checked=True)
            self.assertFalse(line.departed_on)
        # The educator reads neither the restriction nor its detail.
        with self.assertRaises(AccessError):
            self.env["bf.school.childcare.restriction"].with_user(self.educator).search([])
        with freeze_time(self._at(self.monday, 17.0)):
            line._school_depart("guardian", partner=self.mom.partner_id)

    def test_authorized_person_needs_an_id_check_once(self):
        self._registration(lines={self.after: ["mon", "tue"]})
        person = self.Person._school_add_by(self.mom.partner_id, self.alice, "Tante Lucie", "tante", "",
                                            self.today)
        line = self._arrived()
        option = [o for o in line._school_permitted_options() if o["kind"] == "authorized"]
        self.assertTrue(option and option[0]["needs_id_check"])
        with freeze_time(self._at(self.monday, 17.4)), self.assertRaisesRegex(UserError, "ID"):
            line._school_depart("authorized", person=person)
        with freeze_time(self._at(self.monday, 17.5)):
            line._school_depart("authorized", person=person, id_checked=True)
        self.assertEqual((person.id_checked_by_id, line.departure_kind), (self.educator, "authorized"))
        self.assertIn("Tante Lucie", line.departure_label)
        tuesday = self._arrived(sheet=self._sheet(day=self.tuesday))
        with freeze_time(self._at(self.tuesday, 17.5)):
            tuesday._school_depart("authorized", person=person)
        self.assertEqual(tuesday.departure_kind, "authorized", "checked once is enough")

    def test_authorized_person_outside_dates_or_revoked_refused(self):
        self._registration()
        later = self.Person._school_add_by(self.mom.partner_id, self.alice, "Voisin Futur", "voisin", "",
                                           self.monday + timedelta(days=7))
        revoked = self.Person._school_add_by(self.mom.partner_id, self.alice, "Gardienne", "gardienne", "",
                                             self.today)
        revoked._school_remove_by(self.dad.partner_id)
        other_child = self.Person._school_add_by(self.mom.partner_id, self.eli, "Ami d'Éli", "ami", "", self.today)
        line = self._arrived()
        with freeze_time(self._at(self.monday, 16.9)):
            for person in (later, revoked, other_child):
                with self.assertRaisesRegex(UserError, "may not pick up"):
                    line._school_depart("authorized", person=person, id_checked=True)
        self.assertFalse(line.departed_on)

    def test_leaving_alone_on_the_days_and_hours_signed(self):
        self._registration(lines={self.after: ["mon", "tue"]})
        self.env["bf.school.childcare.walk_alone"]._school_sign_by(self.mom.partner_id, self.alice, ["mon"], 16.0,
                                                                   self.today)
        line = self._arrived()
        with freeze_time(self._at(self.monday, 15.75)):
            self.assertFalse([o for o in line._school_permitted_options() if o["kind"] == "walk_alone"])
            with self.assertRaisesRegex(UserError, "leave alone"):
                line._school_depart("walk_alone")
        tuesday = self._arrived(sheet=self._sheet(day=self.tuesday))
        with freeze_time(self._at(self.tuesday, 16.5)), self.assertRaisesRegex(UserError, "leave alone"):
            tuesday._school_depart("walk_alone")
        with freeze_time(self._at(self.monday, 16.25)):
            line._school_depart("walk_alone")
        self.assertEqual(line.departure_kind, "walk_alone")

    def test_someone_else_is_reviewed(self):
        self._registration()
        line = self._arrived()
        with freeze_time(self._at(self.monday, 16.9)), self.assertRaisesRegex(UserError, "Write who came"):
            line._school_depart("other", note="  ")
        with freeze_time(self._at(self.monday, 17.0)):
            line._school_depart("other", note="Julie Voisine, appel de la mère à 16 h")
        activity = line.sheet_id.sudo().activity_ids
        self.assertEqual(activity.user_id, self.manager)

    def test_forged_choice_in_the_departure_window(self):
        self._registration()
        line = self._arrived()
        with freeze_time(self._at(self.monday, 16.9)):
            wizard = line.action_departure()
            departure = self.env["bf.school.childcare.departure"].with_user(self.educator).browse(wizard["res_id"])
            self.assertEqual(len(departure.choice_ids), 3)
            forged = self.env["bf.school.childcare.departure.choice"].with_user(self.educator).create(
                {"wizard_id": departure.id, "kind": "guardian", "label": "Maman", "partner_ref": self.other.partner_id.id})
            with self.assertRaisesRegex(UserError, "may not pick up"):
                forged.action_depart()
        mom = departure.choice_ids.filtered(lambda c: c.partner_ref == self.mom.partner_id.id)
        with freeze_time(self._at(self.monday, 17.0)):
            mom.action_depart()
        self.assertEqual(line.departure_guardian_id, self.mom.partner_id)

    # --- Lateness on the school's clock -----------------------------------------------------

    def test_lateness_after_the_grace(self):
        self._registration()
        line = self._arrived()
        with freeze_time(self._at(self.monday, 18.0 + 20 / 60)):
            line._school_depart("guardian", partner=self.dad.partner_id)
        self.assertEqual(line.late_minutes, 15, "18:00, 5 minutes of grace, gone at 18:20")
        self.assertEqual(line.departed_time, "18:20")

    def test_lateness_between_8_pm_and_midnight(self):
        # 🔴 A period ending at 20:30 in Montréal ends at 00:30 UTC the next day.
        evening = self.env["bf.school.childcare.slot"].create({
            "name": "Soirée", "school_id": self.school.id, "kind": "after", "start_time": 18.0, "end_time": 20.5})
        self._registration(lines={evening: ["mon"]})
        with freeze_time(self._at(self.monday, 22.0)):
            action = self.env["bf.school.childcare.sheet"].with_user(self.educator)._action_school_today()
            self.assertEqual(self.school._calendar_today(), self.monday)
        sheets = self.env["bf.school.childcare.sheet"].browse(action["domain"][0][2])
        sheet = sheets.filtered(lambda s: s.slot_id == evening)
        self.assertEqual(sheet.date, self.monday, "today is the school's, not the server's UTC date")
        line = self._arrived(sheet=sheet, hour=18.2)
        departed = self._at(self.monday, 21 + 10 / 60)
        self.assertEqual(departed.date(), self.monday + timedelta(days=1), "UTC is already the next day")
        with freeze_time(departed):
            line._school_depart("guardian", partner=self.mom.partner_id)
        self.assertEqual((line.late_minutes, line.departed_time), (40, "21:10"))

    # --- Closing, roles ---------------------------------------------------------------------

    def test_closing(self):
        self._registration(lines={self.after: ["mon"], self.before: ["mon"]})
        self._registration(student=self.bruno, lines={self.after: ["mon"]})
        sheet = self._sheet()
        alice = self._arrived(sheet=sheet)
        # A sheet closes on its day.
        with freeze_time(self._at(self.monday, 18.0)):
            with self.assertRaisesRegex(UserError, "Alice Garde"):
                sheet.with_user(self.educator).action_close()
            with freeze_time(self._at(self.monday, 17.0)):
                alice._school_depart("guardian", partner=self.mom.partner_id)
            sheet.with_user(self.educator).action_close()
            self.assertEqual((sheet.state, self._line(sheet, self.bruno).presence), ("closed", "absent"))
            # Closing returns today's sheets, opened if need be: the morning one is among them.
            morning = self.env["bf.school.childcare.sheet"].search(
                [("slot_id", "=", self.before.id), ("date", "=", self.monday)])
            self.assertTrue(morning)
            self._arrived(sheet=morning, hour=7.2)
            morning.with_user(self.educator).action_close()
            self.assertEqual(self._line(morning, self.alice).departure_kind, "class")
            # Closed: out of the educator's reach, reopened by the manager only.
            with self.assertRaises(AccessError):
                sheet.with_user(self.educator).read(["state"])
            with self.assertRaises(AccessError):
                sheet.with_user(self.educator).action_reopen()
            sheet.with_user(self.manager).action_reopen()
            self.assertEqual(sheet.state, "open")

    def test_educator_reads_the_child_not_the_family(self):
        self._registration()
        self._book(student=self.eli)
        line = self._arrived()
        for model in ("bf.school.guardian.link", "bf.school.childcare.registration", "bf.school.childcare.booking",
                      "bf.school.childcare.pickup.person", "bf.school.childcare.walk_alone",
                      "bf.school.childcare.restriction", "bf.school.enrollment"):
            with self.assertRaises(AccessError, msg=model):
                self.env[model].with_user(self.educator).search([], limit=1)
        self.assertIn("Maman Garde", line.permitted_summary)
        self.assertEqual(line.read(["alert", "permitted_summary", "restriction_names"])[0]["restriction_names"], "")
        self.assertNotIn("registration_id", line.fields_get())
        with self.assertRaises(AccessError):
            self.env["bf.school.childcare.registration"].with_user(self.educator).browse(
                line.sudo().registration_id.id).read(["state"])
