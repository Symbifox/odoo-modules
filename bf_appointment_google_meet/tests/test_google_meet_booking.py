"""Google Meet on appointment bookings.

Google is not reachable from a test. `FakeGoogle` stands in for the Calendar
v3 service and keeps the one piece of state that matters: the Meet attached to
the event. Everything above it is real: the booking, its confirmation, the
event OCA creates, the routing to the host's Google config and the push made
by `calendar_nextcloud_sync`.
"""

from datetime import timedelta
from unittest.mock import patch

import pytz

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged

MEET_URL = "https://meet.google.com/abc-defg-hij"
TALK_URL = "https://cloud.test.invalid/call/talk1234"
GOOGLE_ID = "gid0001"
MODULE = "odoo.addons.bf_appointment_google_meet.models.resource_booking"


class _Exec:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


def _http_error(status):
    import httplib2
    from googleapiclient.errors import HttpError
    return HttpError(httplib2.Response({"status": status}), b'{"error": {}}')


class FakeGoogle:
    """Calendar v3 double for one event on the host's primary calendar."""

    def __init__(self, existing_link=None, pending=0, fail=None, status="confirmed",
                 crash=False, push_fail=False):
        self.link = existing_link      # Meet currently attached to the event
        self.pending = pending         # gets answered `pending` before the link
        self.fail = fail               # HTTP status raised by get/patch
        self.status = status
        self.crash = crash             # get raises a non-Google error
        self.push_fail = push_fail     # the sync's own insert/patch are refused
        self.requested = False
        self.calls = []

    def events(self):
        return self

    def _event(self):
        event = {"id": GOOGLE_ID, "etag": '"e%d"' % len(self.calls), "status": self.status}
        if self.requested and self.pending:
            event["conferenceData"] = {"createRequest": {"status": {"statusCode": "pending"}}}
        elif self.link:
            event["hangoutLink"] = self.link
        return event

    def insert(self, **kw):
        self.calls.append(("insert", kw))

        def run():
            if self.push_fail:
                raise _http_error(500)
            return {"id": GOOGLE_ID, "etag": '"e0"'}
        return _Exec(run)

    def get(self, **kw):
        self.calls.append(("get", kw))

        def run():
            if self.crash:
                raise RuntimeError("boom")
            if self.fail:
                raise _http_error(self.fail)
            if self.requested and self.pending:
                self.pending -= 1
                if not self.pending:
                    self.link = MEET_URL
                    return self._event()
                return {"id": GOOGLE_ID, "conferenceData": {
                    "createRequest": {"status": {"statusCode": "pending"}}}}
            return self._event()
        return _Exec(run)

    def patch(self, **kw):
        self.calls.append(("patch", kw))

        def run():
            body = kw.get("body", {})
            if "conferenceData" in body and body["conferenceData"] is None:
                self.requested = False
                self.pending = 0
                self.link = None
                return self._event()
            if "conferenceData" in body:
                if self.fail:
                    raise _http_error(self.fail)
                self.requested = True
                if not self.pending:
                    self.link = MEET_URL
            elif self.push_fail:
                raise _http_error(500)
            return self._event()
        return _Exec(run)

    def delete(self, **kw):
        self.calls.append(("delete", kw))
        return _Exec(lambda: None)

    def conference_patches(self):
        return [kw for name, kw in self.calls
                if name == "patch" and kw.get("body", {}).get("conferenceData")]

    def withdrawals(self):
        return [kw for name, kw in self.calls
                if name == "patch" and "conferenceData" in kw.get("body", {})
                and kw["body"]["conferenceData"] is None]


@tagged("post_install", "-at_install", "bf_appointment_google_meet")
class TestGoogleMeetBooking(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, tz="UTC"))
        cls.host = cls.env["res.users"].create({
            "name": "Meet Host",
            "login": "bf_meet_host",
            "email": "host@test.invalid",
            "tz": "UTC",
            # The notes are written in the host's language; pin it so the
            # assertions read the English source.
            "lang": "en_US",
            "groups_id": [Command.set([cls.env.ref("base.group_user").id])],
        })
        cls.host.sudo().x_google_oauth_refresh_token_encrypted = "enc-token"
        cls.config = cls.env["nextcloud.calendar.sync.config"].sudo().create({
            "name": "Google — Meet Host",
            "backend_type": "google",
            "google_calendar_id": "primary",
            "calendar_owner_id": cls.host.id,
            "sync_direction": "both",
        })
        attendances = [
            Command.create({
                "name": f"All day {d}", "dayofweek": str(d),
                "hour_from": 0.0, "hour_to": 24.0, "day_period": "morning",
            })
            for d in range(7)
        ]
        cls.calendar = cls.env["resource.calendar"].create({
            "name": "24/7 Meet", "attendance_ids": attendances, "tz": "UTC",
        })
        cls.resource = cls.env["resource.resource"].create({
            "name": "Meet Host",
            "resource_type": "user",
            "user_id": cls.host.id,
            "calendar_id": cls.calendar.id,
            "tz": "UTC",
        })
        cls.combination = cls.env["resource.booking.combination"].create({
            "resource_ids": [Command.set([cls.resource.id])],
        })
        cls.partner = cls.env["res.partner"].create({
            "name": "Meet Client", "email": "client@test.invalid",
        })
        cls.booking_type = cls.env["resource.booking.type"].create({
            "name": "Meet Type",
            "duration": 1.0,
            "slot_duration": 1.0,
            "modifications_deadline": 0.0,
            "combination_assignment": "sorted",
            "resource_calendar_id": cls.calendar.id,
            "video_provider": "google_meet",
            "requires_recording_consent": False,
            "combination_rel_ids": [
                Command.create({"sequence": 0, "combination_id": cls.combination.id}),
            ],
        })

    # -- helpers -------------------------------------------------------------

    def _slots(self, n=1):
        now = fields.Datetime.context_timestamp(
            self.env["resource.booking"], fields.Datetime.now())
        slots = self.booking_type._bf_candidate_slots(
            now + timedelta(hours=1), now + timedelta(days=7), limit=n)
        self.assertGreaterEqual(len(slots), n)
        return [s.astimezone(pytz.utc).replace(tzinfo=None) for s in slots]

    def _confirmed(self, google, slot_index=0):
        slot = self._slots(slot_index + 1)[slot_index]
        with self._google(google), self._talk():
            booking = self.booking_type._bf_create_booking(
                slot, partners=self.partner, confirm=False)
            booking.action_confirm()
        return booking

    def _google(self, google):
        return patch.object(
            type(self.env["calendar.google.backend"]), "_build_service",
            return_value=google)

    def _talk(self):
        return patch.object(
            type(self.env["resource.booking"]), "_nc_talk_url_with_fallback",
            return_value=TALK_URL)

    def _no_sleep(self):
        return patch(MODULE + ".time.sleep")

    def _log_bodies(self, booking):
        return " ".join(str(m.body) for m in booking.message_ids)

    # -- the choice exists ---------------------------------------------------

    def test_google_meet_is_a_provider_choice(self):
        choices = dict(self.env["resource.booking.type"]._fields["video_provider"]
                       ._description_selection(self.env))
        self.assertIn("google_meet", choices)
        self.assertEqual(choices["google_meet"], "Google Meet")

    # -- the Meet is attached to the host's real event ------------------------

    def test_confirm_attaches_meet_to_the_hosts_google_event(self):
        google = FakeGoogle()
        booking = self._confirmed(google)
        self.assertEqual(booking.videocall_location, MEET_URL)
        self.assertEqual(booking.meeting_id.videocall_location, MEET_URL)
        self.assertEqual(booking.meeting_id.x_nc_calendar_id, self.config)
        self.assertEqual(booking.meeting_id.x_nc_uid, GOOGLE_ID)
        self.assertTrue(booking.video_room_token,
                        "the token is the controller's trace of a confirmation")
        patches = google.conference_patches()
        self.assertEqual(len(patches), 1, "one conference request, on the real event")
        request = patches[0]
        self.assertEqual(request["eventId"], GOOGLE_ID)
        self.assertEqual(request["calendarId"], "primary")
        self.assertEqual(request["conferenceDataVersion"], 1)
        self.assertEqual(request["sendUpdates"], "none")
        self.assertEqual(
            request["body"]["conferenceData"]["createRequest"]
            ["conferenceSolutionKey"]["type"], "hangoutsMeet")
        self.assertFalse(google.fail)
        self.assertNotIn("Talk room is used instead", self._log_bodies(booking))

    def test_no_throwaway_event_is_inserted(self):
        """Only the booking's own event reaches Google, never a placeholder."""
        google = FakeGoogle()
        self._confirmed(google)
        inserts = [kw for name, kw in google.calls if name == "insert"]
        self.assertEqual(len(inserts), 1, "the push of the booking's event, nothing else")
        self.assertFalse([1 for name, _kw in google.calls if name == "delete"])

    def test_later_pushes_leave_the_conference_alone(self):
        """Every other patch goes out without conferenceDataVersion.

        That is what keeps the Meet when the booking is moved or renamed: Google
        ignores conference data from a client that does not declare a version.
        """
        google = FakeGoogle()
        booking = self._confirmed(google)
        with self._google(google):
            booking.meeting_id.write({"name": "Renamed"})
        others = [kw for name, kw in google.calls
                  if name == "patch" and "conferenceData" not in kw.get("body", {})]
        self.assertTrue(others, "the rename must have been pushed")
        for kw in others:
            self.assertNotIn("conferenceDataVersion", kw)

    def test_pending_conference_is_polled(self):
        google = FakeGoogle(pending=2)
        with self._no_sleep():
            booking = self._confirmed(google)
        self.assertEqual(booking.videocall_location, MEET_URL)

    def test_existing_meet_is_reused_without_a_new_request(self):
        google = FakeGoogle(existing_link=MEET_URL)
        booking = self._confirmed(google)
        self.assertEqual(booking.videocall_location, MEET_URL)
        self.assertFalse(google.conference_patches())

    def test_a_move_keeps_the_same_meet(self):
        google = FakeGoogle()
        booking = self._confirmed(google)
        with self._google(google), self._talk():
            url = booking._generate_video_url()
        self.assertEqual(url, MEET_URL)
        self.assertEqual(len(google.conference_patches()), 1,
                         "no second conference for the same booking")

    # -- fallback to Talk ----------------------------------------------------

    def test_google_refusal_falls_back_to_talk_and_tells_the_host(self):
        google = FakeGoogle(fail=403)
        booking = self._confirmed(google)
        self.assertEqual(booking.videocall_location, TALK_URL)
        self.assertIn("Talk room is used instead", self._log_bodies(booking))
        self.assertIn("403", self._log_bodies(booking))
        self.assertFalse(google.withdrawals(), "nothing was requested, nothing to withdraw")

    def test_no_link_in_time_falls_back_to_talk(self):
        google = FakeGoogle(pending=99)
        with self._no_sleep():
            booking = self._confirmed(google)
        self.assertEqual(booking.videocall_location, TALK_URL)
        withdrawals = google.withdrawals()
        self.assertEqual(len(withdrawals), 1,
                         "the pending request is withdrawn: no second room")
        self.assertEqual(withdrawals[0]["conferenceDataVersion"], 1)
        self.assertEqual(withdrawals[0]["sendUpdates"], "none")
        self.assertFalse(google.link)

    def test_host_without_google_calendar_falls_back_to_talk(self):
        self.config.active = False
        google = FakeGoogle()
        booking = self._confirmed(google)
        self.assertEqual(booking.videocall_location, TALK_URL)
        self.assertFalse(google.calls, "Google is never called for this event")
        self.assertIn("not synced to a Google Calendar", self._log_bodies(booking))

    def _reset_link(self, booking, google):
        """Back to a first confirmation, as if no link had been made yet."""
        with self._google(google):
            booking.write({"videocall_location": False, "video_room_token": False})

    def test_event_on_someone_elses_google_calendar_falls_back(self):
        """A change of host leaves the event on the previous host's calendar.

        `_sync_meeting` rewrites the organizer to the new host at every write,
        but the event keeps the sync config it was routed to at creation: the
        previous host's. The Meet would then belong to someone who is not
        running the meeting. Modelled by handing that config to someone else.
        """
        google = FakeGoogle()
        booking = self._confirmed(google)
        other = self.env["res.users"].create({
            "name": "Other", "login": "bf_meet_other",
            "groups_id": [Command.set([self.env.ref("base.group_user").id])],
        })
        self._reset_link(booking, google)
        self.config.calendar_owner_id = other
        google.link = None
        with self._google(google), self._talk():
            url = booking._generate_video_url()
        self.assertEqual(url, TALK_URL)
        self.assertEqual(len(google.conference_patches()), 1,
                         "only the first confirmation asked for a Meet")

    def test_event_not_yet_on_google_falls_back(self):
        """Google refused the push: the event still carries Odoo's own UID."""
        google = FakeGoogle(push_fail=True)
        booking = self._confirmed(google)
        self.assertIn("@", booking.meeting_id.x_nc_uid)
        self.assertEqual(booking.videocall_location, TALK_URL)
        self.assertFalse(google.conference_patches())
        self.assertIn("not reached Google Calendar yet", self._log_bodies(booking))

    def test_deleted_google_event_falls_back(self):
        google = FakeGoogle(status="cancelled")
        booking = self._confirmed(google)
        self.assertEqual(booking.videocall_location, TALK_URL)

    def test_a_booking_on_its_talk_fallback_stays_on_talk(self):
        """The link the client received does not change on a move."""
        google = FakeGoogle(fail=403)
        booking = self._confirmed(google)
        self.assertEqual(booking.videocall_location, TALK_URL)
        google.fail = None
        before = len(google.calls)
        with self._google(google), patch.object(
            type(self.env["resource.booking"]), "_nc_talk_reuse_room",
            return_value=TALK_URL,
        ):
            url = booking._generate_video_url()
        self.assertEqual(url, TALK_URL)
        self.assertEqual(len(google.calls), before, "Google is not asked again")

    def test_a_confirmation_never_fails_over_its_link(self):
        google = FakeGoogle(crash=True)
        booking = self._confirmed(google)
        self.assertEqual(booking.state, "confirmed")
        self.assertEqual(booking.videocall_location, TALK_URL)
        self.assertIn("boom", self._log_bodies(booking))

    # -- a move keeps the Meet the client received -----------------------------

    def _move_with(self, booking, google):
        with self._google(google), self._talk():
            return booking._generate_video_url()

    def test_a_move_during_a_google_outage_keeps_the_meet(self):
        google = FakeGoogle()
        booking = self._confirmed(google)
        for failure in (500, 401):
            google.fail = failure
            self.assertEqual(self._move_with(booking, google), MEET_URL,
                             "HTTP %s is temporary: the Meet stays" % failure)
        self.assertIn("Its Meet link is kept", self._log_bodies(booking))
        google.fail = None
        google.crash = True
        self.assertEqual(self._move_with(booking, google), MEET_URL)

    def test_a_move_after_the_event_was_deleted_goes_to_talk(self):
        google = FakeGoogle()
        booking = self._confirmed(google)
        google.fail = 404
        self.assertEqual(self._move_with(booking, google), TALK_URL)
        google.fail = None
        google.status = "cancelled"
        self.assertEqual(self._move_with(booking, google), TALK_URL)

    # -- whoever confirms, the host's account is used --------------------------

    def test_the_assistant_confirms_for_the_host(self):
        """The host's Google token is reached as the push reaches it.

        The double enforces the guard of `_get_google_credentials`: only the
        user themself, an administrator or a superuser environment.
        """
        assistant = self.env["res.users"].create({
            "name": "Assistant", "login": "bf_meet_assistant",
            "groups_id": [Command.set([
                self.env.ref("base.group_user").id,
                self.env.ref("resource_booking.group_manager").id,
            ])],
        })
        self.assertFalse(assistant.has_group("base.group_system"))
        google = FakeGoogle()

        def guarded_build(backend, user):
            env = user.env
            if not (user.id == env.uid or env.su
                    or env.user.has_group("base.group_system")):
                from odoo.exceptions import AccessError
                raise AccessError("another user's Google credentials")
            return google

        slot = self._slots()[0]
        with patch.object(type(self.env["calendar.google.backend"]),
                          "_build_service", guarded_build), self._talk():
            booking = self.booking_type._bf_create_booking(
                slot, partners=self.partner, confirm=False)
            # `_bf_create_booking` hands back a sudo record; the back office
            # confirms as the assistant, without it.
            as_assistant = booking.with_user(assistant)
            self.assertFalse(as_assistant.env.su)
            as_assistant.action_confirm()
        self.assertEqual(booking.videocall_location, MEET_URL)
        self.assertEqual(len(google.conference_patches()), 1)

    def test_the_note_is_in_the_hosts_language(self):
        """Reason and sentence both in French for a French-speaking host."""
        self.assertTrue(self.env["res.lang"]._lang_get("fr_CA"), "fr_CA must be active")
        self.host.lang = "fr_CA"
        google = FakeGoogle(fail=403)
        # The person confirming reads English: the note must not follow them.
        english_type = self.booking_type.with_context(lang="en_US")
        with self._google(google), self._talk():
            booking = english_type._bf_create_booking(
                self._slots()[0], partners=self.partner, confirm=False)
            self.assertEqual(booking.env.lang, "en_US")
            booking.action_confirm()
        log = self._log_bodies(booking)
        self.assertIn("Une salle Nextcloud Talk le remplace", log)
        self.assertIn("Google a refusé la demande (HTTP 403)", log)

    # -- other providers are untouched ---------------------------------------

    def test_other_providers_go_through_the_parent(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_appointment.jitsi_domain", "meet.test.invalid")
        self.booking_type.video_provider = "jitsi"
        google = FakeGoogle()
        booking = self._confirmed(google)
        self.assertTrue(booking.videocall_location.startswith("https://meet.test.invalid/"))
        self.assertFalse(google.conference_patches())
