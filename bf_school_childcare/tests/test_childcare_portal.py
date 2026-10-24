import re
from datetime import timedelta

from freezegun import freeze_time

from odoo.tests import HttpCase, tagged

from .common import ChildcareFixture

#: English left in a French email, or a template key left raw.
ENGLISH = ("Hello", "child care", "Child care", "may now", "may no longer", "Change made by", "is cancelled",
           "is confirmed", "receives this notice", "the portal", "registration", "Every adult", "leave")
RAW = ("{{", "}}", "t-out", "object.", "ctx.get", "t-if")
PAGE = "/my/school/childcare"


@tagged("post_install", "-at_install")
class TestChildcarePortal(ChildcareFixture, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_childcare()

    def _csrf(self):
        page = self.url_open("/my/account").text
        return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)

    def _post(self, url, **data):
        return self.url_open(url, data=dict(data, csrf_token=self._csrf()), allow_redirects=False)

    def test_page_and_button(self):
        self.authenticate("cc_mom", "cc_mom")
        home = self.url_open("/my/school").text
        self.assertIn(PAGE, home)
        page = self.url_open(PAGE).text
        self.assertIn("Alice Garde", page)
        self.assertIn("Bruno Garde", page)
        self.assertNotIn("Dina Autre", page)

    def test_adult_without_parental_authority_refused(self):
        self.authenticate("cc_grandma", "cc_grandma")
        self.assertNotIn(PAGE, self.url_open("/my/school").text, "no button without parental authority")
        page = self.url_open(PAGE).text
        self.assertNotIn("Alice Garde", page)
        for url, data in ((PAGE + "/book", {"student_id": self.alice.id, "slot_id": self.after.id,
                                            "date": str(self.monday)}),
                          (PAGE + "/person/add", {"student_id": self.alice.id, "name": "Quelqu'un",
                                                  "date_from": str(self.monday)}),
                          (PAGE + "/walk_alone/sign", {"student_id": self.alice.id, "from_time": "16:00",
                                                       "date_from": str(self.monday), "day_mon": "on",
                                                       "accept": "on"}),
                          (PAGE + "/absence", {"student_id": self.alice.id, "date": str(self.monday)})):
            self.assertEqual(self._post(url, **data).status_code, 404, url)
        self.assertFalse(self.env["bf.school.childcare.booking"].search([("student_id", "=", self.alice.id)]))
        self.assertFalse(self.env["bf.school.childcare.pickup.person"].search([("student_id", "=", self.alice.id)]))

    def test_period_id_not_a_number(self):
        self.authenticate("cc_mom", "cc_mom")
        for url in (PAGE + "/book", PAGE + "/absence"):
            response = self._post(url, student_id=self.alice.id, slot_id="abc", date=str(self.monday))
            self.assertEqual(response.status_code, 404, url)

    def test_ids_of_another_family_refused(self):
        theirs = self._book(student=self.dina, parent=self.other)
        person = self.env["bf.school.childcare.pickup.person"]._school_add_by(
            self.other.partner_id, self.dina, "Voisin de Dina", "voisin", "", self.today)
        walk = self.env["bf.school.childcare.walk_alone"]._school_sign_by(
            self.other.partner_id, self.dina, ["mon"], 16.0, self.today)
        self.authenticate("cc_mom", "cc_mom")
        for url, data in ((PAGE + "/book", {"student_id": self.dina.id, "slot_id": self.after.id,
                                            "date": str(self.monday)}),
                          (PAGE + "/booking/%s/cancel" % theirs.id, {}),
                          (PAGE + "/person/%s/remove" % person.id, {}),
                          (PAGE + "/walk_alone/%s/revoke" % walk.id, {}),
                          (PAGE + "/register", {"student_id": self.dina.id, "date_start": str(self.monday),
                                                "slot_%s_mon" % self.after.id: "on"})):
            self.assertEqual(self._post(url, **data).status_code, 404, url)
        self.assertEqual((theirs.state, person.state, walk.state), ("booked", "active", "active"))
        # A period of another school, slipped into the form, is not found either.
        elsewhere = self.env["bf.school"].create({"name": "Autre école"})
        slot = self.env["bf.school.childcare.slot"].create({"name": "Ailleurs", "school_id": elsewhere.id,
                                                            "kind": "after", "start_time": 15.5, "end_time": 18.0})
        response = self._post(PAGE + "/book", student_id=self.alice.id, slot_id=slot.id, date=str(self.monday))
        self.assertEqual(response.status_code, 404)

    def test_family_flow(self):
        self.authenticate("cc_mom", "cc_mom")
        response = self._post(PAGE + "/book", student_id=self.alice.id, slot_id=self.after.id, date=str(self.monday))
        self.assertEqual(response.status_code, 303)
        booking = self.env["bf.school.childcare.booking"].search([("student_id", "=", self.alice.id)])
        self.assertEqual((booking.state, booking.booked_by_id), ("booked", self.mom.partner_id))
        self.assertIn("alert-success", self.url_open(PAGE).text)
        self._post(PAGE + "/booking/%s/cancel" % booking.id)
        self.assertEqual(booking.state, "cancelled")
        refused = self._post(PAGE + "/book", student_id=self.alice.id, slot_id=self.full.id, date=str(self.monday))
        self.assertEqual(refused.status_code, 303)
        self.assertIn("alert-danger", self.url_open(PAGE).text, "the refusal is shown, nothing is booked")
        self._post(PAGE + "/absence", student_id=self.alice.id, date=str(self.monday), slot_id=self.after.id,
                   reason="Dentiste")
        absence = self.env["bf.school.childcare.absence"].search([("student_id", "=", self.alice.id)])
        self.assertEqual((absence.channel, absence.declared_by_id, absence.slot_id),
                         ("portal", self.mom.partner_id, self.after))
        self._post(PAGE + "/person/add", student_id=self.alice.id, name="Tante Lucie", relationship="tante",
                   phone="819 555-0101", date_from=str(self.today))
        person = self.env["bf.school.childcare.pickup.person"].search([("student_id", "=", self.alice.id)])
        self.assertEqual((person.name, person.channel, person.state), ("Tante Lucie", "portal", "active"))
        self.assertIn("Tante Lucie", self.url_open(PAGE).text)
        self._post(PAGE + "/person/%s/remove" % person.id)
        self.assertEqual(person.state, "revoked")
        self._post(PAGE + "/walk_alone/sign", student_id=self.alice.id, from_time="15:45", date_from=str(self.today),
                   day_mon="on", day_thu="on", accept="on")
        walk = self.env["bf.school.childcare.walk_alone"].search([("student_id", "=", self.alice.id)])
        self.assertEqual((walk.state, walk.from_time, walk.mon, walk.tue, walk.thu), ("active", 15.75, True, False, True))
        self._post(PAGE + "/walk_alone/%s/revoke" % walk.id)
        self.assertEqual(walk.state, "revoked")
        self._post(PAGE + "/register", student_id=self.bruno.id, date_start=str(self.monday),
                   **{"slot_%s_mon" % self.after_k.id: "on", "slot_%s_fri" % self.after_k.id: "on"})
        registration = self.env["bf.school.childcare.registration"].search([("student_id", "=", self.bruno.id)])
        self.assertEqual((registration.state, registration.periods_per_week, registration.requested_by_id),
                         ("draft", 2, self.mom.partner_id))

    def test_departures_shown_on_the_school_s_clock(self):
        self._registration()
        sheet = self._sheet()
        line = self._line(sheet, self.alice).with_user(self.educator)
        with freeze_time(self._at(self.monday, 15.6)):
            line.action_arrive()
        with freeze_time(self._at(self.monday, 17.0)):
            line._school_depart("guardian", partner=self.dad.partner_id)
        self.authenticate("cc_mom", "cc_mom")
        page = self.url_open(PAGE).text
        self.assertIn(line.departed_time, page)
        self.assertIn("Papa Garde", page)

    # --- Emails in French -------------------------------------------------------------------

    def _assert_french(self, mails, expected):
        self.assertTrue(mails, expected)
        for mail in mails:
            text = "%s\n%s" % (mail.subject, mail.body_html)
            for word in ENGLISH:
                self.assertNotIn(word, text, "%s: English left (%s)" % (expected, word))
            for key in RAW:
                self.assertNotIn(key, text, "%s: raw key (%s)" % (expected, key))
            self.assertIn(expected, text)

    def test_every_email_in_french(self):
        self._registration()
        self._assert_french(self._mails(self.mom.partner_id, "bf.school.childcare.registration"),
                            "Bonjour")
        Person = self.env["bf.school.childcare.pickup.person"]
        person = Person._school_add_by(self.mom.partner_id, self.alice, "Tante Lucie", "tante", "", self.today)
        added = self._mails(self.dad.partner_id, Person._name)
        self._assert_french(added, "peut maintenant venir chercher")
        person._school_remove_by(self.mom.partner_id)
        self._assert_french(self._mails(self.dad.partner_id, Person._name) - added, "ne peut plus venir chercher")
        Walk = self.env["bf.school.childcare.walk_alone"]
        walk = Walk._school_sign_by(self.mom.partner_id, self.alice, ["mon"], 16.0, self.today)
        signed = self._mails(self.dad.partner_id, Walk._name)
        self._assert_french(signed, "quitter le service de garde sans adulte")
        self.assertIn("16:00", signed.body_html)
        walk._school_revoke_by(self.dad.partner_id)
        self._assert_french(self._mails(self.dad.partner_id, Walk._name) - signed, "révoquée")
        activity = self.env["bf.school.childcare.activity"].with_user(self.manager).create({
            "name": "Cabane à sucre", "slot_id": self.full.id, "date": self.pedagogical})
        booking = self._book(slot=self.full, day=self.pedagogical)
        activity.with_user(self.manager).action_cancel()
        self._assert_french(self._mails(self.mom.partner_id, booking._name), "Cabane à sucre")
        self.assertEqual(len(self._mails(self.grandma.partner_id, booking._name)), 0)

    def test_dates_default_to_the_school_s_today(self):
        self.authenticate("cc_mom", "cc_mom")
        page = self.url_open(PAGE).text
        self.assertIn('value="%s"' % self.school._calendar_today(), page)
        self.assertNotIn('value="%s"' % (self.school._calendar_today() - timedelta(days=1)), page)
