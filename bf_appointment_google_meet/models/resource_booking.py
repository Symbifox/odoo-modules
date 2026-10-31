"""Google Meet for appointment bookings, attached to the host's real event.

`calendar_nextcloud_sync` pushes the booking's calendar event to the Google
Calendar of its organizer (the host) as soon as the event is created, with the
client among the guests and `sendUpdates="none"`. This module asks Google to
add a Meet to THAT event, rather than to a throwaway one: the link lives with
the real meeting, and a client signed in to Google with the invited address
joins without having to ask to be let in.

Order of things at confirmation (`bf_appointment.action_confirm`): OCA creates
or updates the event, which pushes it to Google; then `_generate_video_url`
runs, and its result is written on the booking, which rewrites the event and
patches Google again. Those later patches go out without
`conferenceDataVersion`, which Google reads as "leave the conference alone".

When the Meet cannot be had, the booking falls back to Nextcloud Talk, exactly
as a Talk type would, and the reason is logged on the booking for the host.
Two exceptions keep the link the client already received:

- a booking already on Talk stays on Talk;
- a booking already on Meet keeps its Meet when Google only fails for now
  (network, server error, expired token). It moves to Talk only when the Meet
  is gone for good: event deleted, no longer on the host's Google Calendar.
"""

import logging
import time
import uuid

from odoo import _, exceptions, models

_logger = logging.getLogger(__name__)

try:
    from googleapiclient.errors import HttpError
except ImportError:  # the dependency declares it; this only keeps imports safe
    HttpError = Exception

GOOGLE_MEET = "google_meet"
# Conference creation can be asynchronous: Google answers `pending` and fills
# in the link a moment later. A few short polls cover it without holding the
# confirmation for long.
_PENDING_POLLS = 3
_PENDING_DELAY = 1.0


def _is_pending(event):
    request = (event.get("conferenceData") or {}).get("createRequest") or {}
    return (request.get("status") or {}).get("statusCode") == "pending"


def is_meet_link(url):
    return "meet.google.com/" in (url or "")


def _http_status(exc):
    return getattr(getattr(exc, "resp", None), "status", None)


class ResourceBooking(models.Model):
    _inherit = "resource.booking"

    def _generate_video_url(self):
        self.ensure_one()
        if self.type_id.video_provider != GOOGLE_MEET:
            return super()._generate_video_url()
        # Same token rule as the parent: set on the first call and read by the
        # public controller as the trace that a confirmation took place.
        deja_generee = bool(self.video_room_token)
        if not deja_generee:
            self.video_room_token = uuid.uuid4().hex[:12]
        current = (self.videocall_location or "").strip()
        # Already on its Talk fallback: stay there. That link is the one the
        # client received, and a move must not swap it for another.
        if deja_generee and current and not is_meet_link(current):
            return self._bf_meet_talk_fallback(deja_generee)
        # Reasons are for the host, in the host's language, also when the
        # client confirms from the public page in another one.
        host = self.with_context(lang=self.user_id.lang or self.env.lang)
        link, reason, definitive = host._bf_google_meet_link()
        if link:
            return link
        if deja_generee and is_meet_link(current) and not definitive:
            _logger.warning(
                "bf_appointment_google_meet: booking %s keeps %s: %s",
                self.id, current, reason,
            )
            host._bf_meet_note(reason, kept=True)
            return current
        _logger.warning(
            "bf_appointment_google_meet: booking %s falls back to Talk: %s",
            self.id, reason,
        )
        host._bf_meet_note(reason, kept=False)
        return self._bf_meet_talk_fallback(deja_generee)

    def _bf_meet_note(self, reason, kept):
        """Note for the host on the booking, in the language of `self`.

        Its own method because `_()` translates into the language of the
        `self` of the function that calls it: called on the host-language
        record, the sentence matches the reason inside it.
        """
        if kept:
            body = _(
                "Google Meet could not be checked for this appointment "
                "(%(reason)s). Its Meet link is kept.",
                reason=reason,
            )
        else:
            body = _(
                "Google Meet could not be created for this appointment "
                "(%(reason)s). A Nextcloud Talk room is used instead.",
                reason=reason,
            )
        self._message_log(body=body)

    def _bf_meet_talk_fallback(self, deja_generee):
        """The parent's Nextcloud Talk branch, for a Google Meet type.

        Mirrors `bf_appointment._generate_video_url` (Talk): a booking that
        already has its own room keeps it on a move; otherwise a new room, or
        the per-booking short link when Talk is unreachable.
        """
        if deja_generee:
            if self._shlink_is_ours():
                return self._nc_talk_room_behind_shlink()
            url = self._nc_talk_reuse_room()
            if url:
                return url
        return self._nc_talk_url_with_fallback()

    def _bf_google_meet_link(self):
        """Meet link of the booking's Google event, created if missing.

        Returns `(link, False, False)`, or `(False, reason, definitive)`:
        `reason` is for the host; `definitive` says the Meet cannot come back
        (as opposed to Google failing for now). Never raises: a confirmation
        must not fail over its video link.
        """
        self.ensure_one()
        event = self.meeting_id
        if not event:
            return False, _("the appointment has no calendar event"), True
        # sudo: the sync config is readable only; and the owner record taken
        # from it carries the superuser flag that `_get_google_credentials`
        # needs when the person confirming is not the host (same as the push).
        config = event.sudo().x_nc_calendar_id
        if not config or config.backend_type != "google" or not config.active:
            return False, _("the event is not synced to a Google Calendar"), True
        if config.sync_direction == "nc_to_odoo":
            return False, _("the host's Google Calendar is synced one way only"), True
        owner = config.calendar_owner_id
        if not owner or owner != event.user_id:
            return False, _("the event is not on the host's own Google Calendar"), True
        google_id = event.x_nc_uid
        # Before its first successful push, the event carries the UID Odoo
        # generated (`<uuid>@<domain>`), not a Google id. The sync retries it.
        if not google_id or "@" in google_id:
            return False, _("the event has not reached Google Calendar yet"), False
        calendar_id = config.google_calendar_id or "primary"
        events = None
        requested = False
        try:
            service = self.env["calendar.google.backend"]._build_service(owner)
            events = service.events()
            gevent = events.get(calendarId=calendar_id, eventId=google_id).execute()
            if gevent.get("status") == "cancelled":
                return False, _("the event was deleted from Google Calendar"), True
            link = gevent.get("hangoutLink")
            if not link:
                requested = True
                gevent = events.patch(
                    calendarId=calendar_id,
                    eventId=google_id,
                    body={"conferenceData": {"createRequest": {
                        "requestId": uuid.uuid4().hex,
                        "conferenceSolutionKey": {"type": "hangoutsMeet"},
                    }}},
                    conferenceDataVersion=1,
                    sendUpdates="none",
                ).execute()
                link = gevent.get("hangoutLink")
                polls = 0
                while not link and polls < _PENDING_POLLS and _is_pending(gevent):
                    polls += 1
                    time.sleep(_PENDING_DELAY)
                    gevent = events.get(
                        calendarId=calendar_id, eventId=google_id,
                    ).execute()
                    link = gevent.get("hangoutLink")
        except HttpError as exc:
            status = _http_status(exc)
            if status in (404, 410):
                return False, _("the event was deleted from Google Calendar"), True
            self._bf_meet_withdraw_request(events, calendar_id, google_id, requested)
            if status == 401:
                return False, _(
                    "Google refused the host's connection: the host must "
                    "reconnect their Google account in Preferences"
                ), False
            return False, _("Google refused the request (HTTP %(code)s)", code=status), False
        except (exceptions.UserError, exceptions.AccessError) as exc:
            # Raised by the host's credentials (account disconnected, token
            # unusable), not by Google: say so, the host can act on it.
            self._bf_meet_withdraw_request(events, calendar_id, google_id, requested)
            return False, _("the host's Google account could not be used (%(err)s)",
                            err=str(exc)[:200]), False
        except Exception as exc:  # noqa: BLE001 — network, library
            self._bf_meet_withdraw_request(events, calendar_id, google_id, requested)
            return False, _("Google could not be reached (%(err)s)",
                            err=str(exc)[:200]), False
        if not link:
            self._bf_meet_withdraw_request(events, calendar_id, google_id, requested)
            return False, _("Google did not return a Meet link in time"), False
        return link, False, False

    def _bf_meet_withdraw_request(self, events, calendar_id, google_id, requested):
        """Best effort: remove a conference asked for but not obtained.

        Google may still attach the Meet after we gave up. The host's event and
        the client's copy would then offer "Join with Google Meet" while the
        email carries the Talk link: two rooms for one meeting.
        """
        if not (requested and events is not None):
            return
        try:
            events.patch(
                calendarId=calendar_id,
                eventId=google_id,
                body={"conferenceData": None},
                conferenceDataVersion=1,
                sendUpdates="none",
            ).execute()
        except Exception as exc:  # noqa: BLE001 — best effort only
            _logger.warning(
                "bf_appointment_google_meet: conference request on %s not "
                "withdrawn: %s", google_id, exc,
            )
