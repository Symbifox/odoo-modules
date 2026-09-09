"""Tests for the identity, revision and slot the .ics has to carry.

The defect these guard is not "the mail did not go out" — it always went out.
It is that the file attached to it named a DIFFERENT meeting every time, so a
calendar client had nothing to update and added a copy instead. Every
assertion here is therefore about what two successive serializations have in
common, never about a single one being well formed.
"""

from datetime import datetime, timedelta

from odoo import Command
from odoo.tests import TransactionCase, tagged


def _ics(event):
    return event._get_ics_file()[event.id].decode("utf-8")


def _prop(ics, name):
    """Value of an ICS property, un-folded (RFC 5545 §3.1 folds at 75 octets)."""
    unfolded = ics.replace("\r\n ", "").replace("\n ", "")
    for line in unfolded.splitlines():
        if line.split(";")[0].split(":")[0] == name:
            return line.split(":", 1)[1]
    return None


@tagged("post_install", "-at_install")
class TestCalendarIcsIdentity(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.guest = cls.env["res.partner"].create({
            "name": "Guest",
            "email": "guest@example.invalid",
        })
        cls.start = datetime.now().replace(microsecond=0) + timedelta(days=7)

    def _event(self, **extra):
        vals = {
            "name": "Meeting",
            "start": self.start,
            "stop": self.start + timedelta(hours=1),
            "partner_ids": [Command.set([self.guest.id])],
        }
        vals.update(extra)
        # ⚠️ No invitation mail from a test. These assertions are about the
        # FILE, and creating an event with a guest sends one with
        # `force_send=True` — a real send attempt against the tenant's real
        # SMTP server, from a test run on the production database.
        return self.env["calendar.event"].with_context(
            no_mail_to_attendees=True
        ).create(vals)

    # -- identity ------------------------------------------------------------

    def test_uid_is_stable_across_serializations(self):
        """The whole point: two .ics for one meeting name the same meeting.

        Core sets no UID and lets vobject invent one per serialization, which
        is what made every update read as a new meeting.
        """
        event = self._event()
        self.assertEqual(_prop(_ics(event), "UID"), _prop(_ics(event), "UID"))

    def test_uid_survives_a_reschedule(self):
        event = self._event()
        before = _prop(_ics(event), "UID")
        event.with_context(no_mail_to_attendees=True).write({"start": self.start + timedelta(days=1),
                     "stop": self.start + timedelta(days=1, hours=1)})
        self.assertEqual(_prop(_ics(event), "UID"), before)

    def test_uid_reuses_the_sync_uid_when_there_is_one(self):
        """Odoo must not mint a second identity next to the remote one."""
        event = self._event()
        if "x_nc_uid" not in event._fields:
            self.skipTest("calendar_nextcloud_sync is not installed")
        event.x_nc_uid = "already-known@remote.invalid"
        self.assertEqual(_prop(_ics(event), "UID"), "already-known@remote.invalid")

    # -- revision ------------------------------------------------------------

    def test_sequence_rises_on_a_material_change(self):
        event = self._event()
        before = int(_prop(_ics(event), "SEQUENCE"))
        event.with_context(no_mail_to_attendees=True).write({"start": self.start + timedelta(days=1),
                     "stop": self.start + timedelta(days=1, hours=1)})
        self.assertEqual(int(_prop(_ics(event), "SEQUENCE")), before + 1)

    def test_sequence_holds_still_for_a_write_that_changes_nothing(self):
        """A save that re-sends the same start is not a new revision.

        The web client posts `start` as a string on every save; comparing raw
        values would bump the revision each time and train clients to re-ask
        guests over nothing.
        """
        event = self._event()
        before = int(_prop(_ics(event), "SEQUENCE"))
        event.with_context(no_mail_to_attendees=True).write({"start": str(event.start), "name": event.name})
        self.assertEqual(int(_prop(_ics(event), "SEQUENCE")), before)

    def test_sequence_ignores_a_description_edit(self):
        event = self._event()
        before = int(_prop(_ics(event), "SEQUENCE"))
        event.with_context(no_mail_to_attendees=True).write({"description": "<p>a note</p>"})
        self.assertEqual(int(_prop(_ics(event), "SEQUENCE")), before)

    # -- recurrence ----------------------------------------------------------

    def _series(self):
        event = self._event(
            recurrency=True, rrule_type="weekly", interval=1,
            count=3, end_type="count",
            mon=True, tue=True, wed=True, thu=True, fri=True, sat=True, sun=True,
        )
        return event.recurrence_id.calendar_event_ids.sorted("start")

    def test_base_event_keeps_the_rrule(self):
        occurrences = self._series()
        base = occurrences.recurrence_id.base_event_id
        self.assertTrue(_prop(_ics(base), "RRULE"))
        self.assertIsNone(_prop(_ics(base), "RECURRENCE-ID"))

    def test_other_occurrences_carry_a_slot_and_no_rrule(self):
        """Core copies the RRULE onto every occurrence.

        Accepted as-is, the .ics of one occurrence writes a whole new series
        into the guest's calendar — measured on a weekly statutory meeting
        whose single moved Thursday went out carrying
        `RRULE:FREQ=WEEKLY;UNTIL=20261030T170000Z`.
        """
        occurrences = self._series()
        second = occurrences[1]
        ics = _ics(second)
        self.assertIsNone(_prop(ics, "RRULE"))
        self.assertTrue(_prop(ics, "RECURRENCE-ID"))

    def test_a_moved_occurrence_keeps_naming_the_slot_it_left(self):
        """RECURRENCE-ID is the ORIGINAL slot, not the new one.

        It answers "which occurrence moved". Reading the new start back into it
        would name an occurrence that never existed.
        """
        occurrences = self._series()
        second = occurrences[1]
        original = second.start
        second.with_context(no_mail_to_attendees=True).write({"start": second.start + timedelta(days=2),
                      "stop": second.stop + timedelta(days=2),
                      "recurrence_update": "self_only"})
        self.assertEqual(second.bf_ics_recurrence_id, original)
        self.assertEqual(
            _prop(_ics(second), "RECURRENCE-ID"),
            original.strftime("%Y%m%dT%H%M%SZ"),
        )

    def test_the_series_and_its_occurrence_share_one_identity(self):
        occurrences = self._series()
        base = occurrences.recurrence_id.base_event_id
        self.assertEqual(_prop(_ics(occurrences[1]), "UID"), _prop(_ics(base), "UID"))

    # -- organiser and description ------------------------------------------

    def test_organizer_is_a_bare_address(self):
        """A mailto: URI takes an address and nothing else (RFC 6068)."""
        organiser = self.env["res.users"].create({
            "name": "Organiser",
            "login": "bf_ics_organiser",
            "email": '"a display name" <organiser@example.invalid>',
        })
        event = self._event(user_id=organiser.id)
        self.assertEqual(
            _prop(_ics(event), "ORGANIZER"), "mailto:organiser@example.invalid",
        )

    def test_description_loses_its_lost_data_uri(self):
        event = self._event(description=(
            '<p>text/html,Lien%20pour%20la%20rencontre%3A%20ici'
            '":Lien pour la rencontre: ici</p>'
        ))
        self.assertEqual(_prop(_ics(event), "DESCRIPTION"),
                         "Lien pour la rencontre: ici")

    def test_a_normal_description_is_left_alone(self):
        event = self._event(description="<p>Bring the deck</p>")
        self.assertEqual(_prop(_ics(event), "DESCRIPTION"), "Bring the deck")
