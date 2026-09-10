"""Tests for cancelling a meeting instead of deleting it.

The lot exists because `calendar.event.unlink()` tells nobody: core's override
only refreshes alarms, so a meeting deleted from the grid vanishes from Odoo
and stays forever in the guests' own calendars. Everything here therefore
walks the three things a cancellation has to do that a delete never did —
leave the meeting visible, give the time back, and (when asked) actually reach
the guests — plus the two it must NOT do: notify twice, or answer the
recurrence question on its own.
"""

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval


@tagged("post_install", "-at_install")
class TestCalendarCancel(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.organiser = cls.env["res.users"].create({
            "name": "Organiser",
            "login": "bf_cancel_organiser",
            "email": "organiser@example.com",
        })
        cls.guest = cls.env["res.partner"].create({
            "name": "Guest One",
            "email": "guest.one@example.com",
        })
        cls.silent_guest = cls.env["res.partner"].create({
            "name": "Guest Without An Address",
        })
        cls.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "https://odoo.example.com")

    def _make_event(self, **values):
        vals = {
            "name": "Project kickoff",
            "start": "2026-09-10 14:00:00",
            "stop": "2026-09-10 15:00:00",
            "user_id": self.organiser.id,
            "partner_ids": [Command.set([self.guest.id])],
        }
        vals.update(values)
        return self.env["calendar.event"].create(vals)

    def _mails_for(self, event):
        return self.env["mail.mail"].sudo().search([
            ("model", "=", "calendar.event"), ("res_id", "=", event.id),
        ])

    # --- what a cancellation does to the meeting -----------------------

    def test_the_meeting_survives_the_cancellation(self):
        """The whole point: it is struck out, not erased."""
        event = self._make_event()
        event._bf_cancel()
        self.assertTrue(event.exists(), "the meeting must stay on the calendar")
        self.assertEqual(event.bf_event_status, "cancelled")

    def test_the_time_goes_back(self):
        """🔴 `show_as` is what actually frees the slot, and it was the gap.

        `resource_calendar._get_bookable_intervals` counts an event as busy on
        `show_as == "busy"` alone. Setting the status without setting `show_as`
        looks cancelled and still blocks the picker — which is exactly the
        state the two events already carrying the status were found in on
        production.
        """
        event = self._make_event()
        self.assertEqual(event.show_as, "busy")
        event._bf_cancel()
        self.assertEqual(event.show_as, "free")

    def test_the_reason_is_kept(self):
        event = self._make_event()
        event._bf_cancel(reason="Client rescheduled by phone")
        self.assertEqual(
            event.bf_cancellation_reason, "Client rescheduled by phone")

    def test_the_revision_is_bumped(self):
        """RFC 5545 §3.8.7.4: a CANCEL at the same SEQUENCE is not newer.

        Nothing material changes when a meeting is cancelled, so the bump that
        rides on `write()` cannot fire here — and a client that receives a
        cancellation no newer than the invitation it holds is entitled to drop
        it on the floor.
        """
        event = self._make_event()
        before = event.bf_ics_sequence
        event._bf_cancel()
        self.assertEqual(event.bf_ics_sequence, before + 1)

    def test_writing_the_status_alone_frees_the_time(self):
        """🔴 The two paths that said the same thing and did not do the same.

        The popover offers "Cancel" in its footer and "Cancelled" in the status
        group two inches away. Until the coupling moved into `write`, the
        second wrote the word and left the slot booked — and it is the one that
        looks like an ordinary field, so it is the one that gets used.
        """
        event = self._make_event()
        event.write({"bf_event_status": "cancelled"})
        self.assertEqual(event.show_as, "free")

    def test_uncancelling_gives_the_time_back_to_the_meeting(self):
        """The way back matters as much as the way in.

        A meeting put back on must stop being invisible to the slot picker, or
        it stays bookable over for good.
        """
        event = self._make_event()
        event._bf_cancel()
        event.write({"bf_event_status": "confirmed"})
        self.assertEqual(event.show_as, "busy")

    def test_an_explicit_show_as_still_wins(self):
        """The coupling is a default, not a lock.

        An all-day "office" event synced from elsewhere arrives free and
        cancelled at once; forcing it back to busy would re-block a slot the
        remote calendar says is open.
        """
        event = self._make_event()
        event.write({"bf_event_status": "cancelled", "show_as": "busy"})
        self.assertEqual(event.show_as, "busy")

    # --- the notice ----------------------------------------------------

    def test_no_notice_unless_asked(self):
        """Unticked by default, and the default has to be real.

        The box is the only thing between a cancellation and an email leaving
        the building, so "off" has to mean nothing is sent even when guests are
        present and addressable.
        """
        event = self._make_event()
        event._bf_cancel()
        self.assertFalse(self._mails_for(event))

    def test_the_notice_reaches_the_guest(self):
        event = self._make_event()
        event._bf_cancel(notify=True)
        mails = self._mails_for(event)
        self.assertEqual(len(mails), 1)
        self.assertIn(self.guest, mails.recipient_ids)

    def test_the_organiser_is_not_written_to(self):
        """They are the one cancelling; they do not need the email."""
        event = self._make_event(
            partner_ids=[Command.set([
                self.guest.id, self.organiser.partner_id.id,
            ])],
        )
        recipients = event._bf_cancellation_recipients()
        self.assertIn(self.guest, recipients)
        self.assertNotIn(self.organiser.partner_id, recipients)

    def test_the_organiser_is_out_on_an_UNSAVED_dialog_too(self):
        """🔴 Le cas que le test d'à côté ne pouvait pas attraper.

        Un assistant créé dans un test porte de vrais identifiants et la
        soustraction marche. Le client web, lui, calcule sur un enregistrement
        NEUF — c'est l'état dans lequel la boîte est lue et cochée — et les
        participants remontent en `NewId(origin=…)`. La soustraction ne retirait
        alors rien : l'organisateur figurait dans la liste des gens à prévenir
        de sa propre annulation. Trouvé au banc navigateur.
        """
        event = self._make_event(
            partner_ids=[Command.set([
                self.guest.id, self.organiser.partner_id.id,
            ])],
        )
        wizard = self.env["bf.calendar.event.cancel"].with_user(
            self.organiser,
        ).with_context(
            default_event_ids=[Command.set(event.ids)],
        ).new({})
        noms = wizard.recipient_ids.mapped("name")
        self.assertIn("Guest One", noms)
        self.assertNotIn(self.organiser.partner_id.name, noms)

    def test_a_guest_with_no_address_is_not_counted(self):
        """Dropped here rather than at send time, so the dialog can say so.

        A guest with no address would otherwise be listed as "will be told" and
        hear nothing, which is the one outcome the dialog exists to prevent.
        """
        event = self._make_event(
            partner_ids=[Command.set([self.silent_guest.id])],
        )
        self.assertFalse(event._bf_cancellation_recipients())
        event._bf_cancel(notify=True)
        self.assertFalse(self._mails_for(event))

    def test_cancelling_twice_writes_once(self):
        """The commonest way to notify twice is a double click."""
        event = self._make_event()
        event._bf_cancel(notify=True)
        self.assertEqual(len(self._mails_for(event)), 1)
        event._bf_cancel(notify=True)
        self.assertEqual(len(self._mails_for(event)), 1)

    # --- the .ics ------------------------------------------------------

    def _ics_of(self, event):
        return event._get_ics_file()[event.id].decode("utf-8")

    def test_the_ics_says_cancelled(self):
        """A notice whose attachment does not say CANCELLED is a paragraph."""
        event = self._make_event()
        self.assertNotIn("STATUS:CANCELLED", self._ics_of(event))
        event._bf_cancel()
        ics = self._ics_of(event)
        self.assertIn("STATUS:CANCELLED", ics)
        self.assertIn("METHOD:CANCEL", ics)

    def test_the_method_is_left_alone_when_nothing_is_cancelled(self):
        """⚠️ The METHOD of an ordinary invitation is none of our business.

        `bf_appointment` sets `METHOD:REQUEST` on the same file, and both
        modules post-process the same `_get_ics_file`. Writing a METHOD here on
        every path would make the winner depend on module load order.
        """
        event = self._make_event()
        self.assertNotIn("METHOD:CANCEL", self._ics_of(event))

    def test_the_uid_does_not_move(self):
        """The cancellation must name the meeting the guest already holds."""
        event = self._make_event()
        uid_before = event._bf_ics_uid_get()
        event._bf_cancel()
        self.assertIn("UID:%s" % uid_before, self._ics_of(event))

    def test_the_attachment_is_not_called_an_invitation(self):
        """The filename follows what the file does.

        `invitation.ics` on a cancellation is read as an invitation by every
        human who scrolls past the attachment without opening it.
        """
        report = self.env.ref("bf_calendar_invite.report_calendar_event_ics")
        event = self._make_event()
        self.assertEqual(
            report._get_report_from_name(report.report_name).report_type,
            "qweb-ics",
        )
        self.assertEqual(
            safe_eval(report.print_report_name, {"object": event}),
            "invitation.ics",
        )
        event._bf_cancel()
        self.assertEqual(
            safe_eval(report.print_report_name, {"object": event}),
            "cancellation.ics",
        )

    # --- what it refuses to do -----------------------------------------

    def test_a_series_is_refused_rather_than_guessed(self):
        """🔴 Answering the recurrence question silently is the failure mode.

        "This event / this and following / all events" cannot be answered from
        a dialog that does not show the choice, and getting it wrong cancels a
        whole series of meetings by email.
        """
        event = self._make_event(recurrency=True, rrule_type="weekly",
                                 count=3, mon=True, event_tz="UTC",
                                 end_type="count")
        self.assertTrue(event._bf_cancel_blocker())
        with self.assertRaises(UserError):
            event._bf_cancel()

    # --- the neighbour -------------------------------------------------

    def test_the_popover_still_targets_the_renderer_odoo_has(self):
        """The chain no Python test can see, held at both ends.

        The strike-through and the Cancel button both live in a patch of
        `AttendeeCalendarCommonPopover`. If core stops using that class for
        this view, or if the patch stops naming it, the feature disappears in
        total silence — no error, no warning, nothing in the console.
        """
        arch = self.env.ref("calendar.view_calendar_event_calendar").arch
        self.assertIn('js_class="attendee_calendar"', arch)
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(
            here, "static", "src", "xml", "calendar_status_popover.xml",
        ), encoding="utf-8") as handle:
            template = handle.read()
        self.assertIn(
            't-inherit="calendar.AttendeeCalendarCommonPopover.footer"', template)
        self.assertIn("bfDisplayCancel", template)

    def test_the_status_field_is_still_in_the_calendar_arch(self):
        """`rawRecord` only carries what the arch declares.

        Dropping the invisible field would take the strike-through AND the
        Cancel/Delete switch with it, silently.
        """
        view = self.env.ref("calendar.view_calendar_event_calendar")
        arch = self.env["calendar.event"].get_view(
            view.id, "calendar")["arch"]
        self.assertIn('name="bf_event_status"', arch)
