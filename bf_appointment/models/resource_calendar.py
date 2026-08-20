"""Booking availability: honour show_as='free' on attended events.

Upstream OCA ``resource_booking`` counts a person-resource as busy whenever
their partner attends a calendar event (state != declined), regardless of the
event's ``show_as`` flag. Google-synced all-day events ("Bureau", working
location, "Occupé(e)") arrive with show_as='free' and the synced user as
attendee, stored 08:00→18:00 UTC = 4h→14h Montréal — so every workday morning
was silently removed from the public slot picker (reported by a tenant,
2026-07-03: the "Questionnaire d'audit initial" type only ever offered
14h/14h30/15h starts Mon-Thu and nothing on Fridays).

Reimplements ``_calendar_event_busy_intervals`` with one change: the attendee
branch only blocks when the event is marked busy, matching the owner branch
and the free/busy semantics of mainstream booking tools. Events linked to
actual resource bookings still always block.
"""

from pytz import UTC

from odoo import api, fields, models

from odoo.addons.resource.models.utils import Intervals
from odoo.addons.resource_booking.models.resource_calendar import Busy


class ResourceCalendar(models.Model):
    _inherit = "resource.calendar"

    @api.model
    def _calendar_event_busy_intervals(
        self, start_dt, end_dt, resource, analyzed_booking_id
    ):
        """Get busy meeting intervals, ignoring show_as='free' invitations."""
        assert start_dt.tzinfo
        assert end_dt.tzinfo
        start_dt, end_dt = (
            fields.Datetime.to_string(dt.astimezone(UTC)) for dt in (start_dt, end_dt)
        )
        intervals = []
        resource_user = (
            resource.resource_type == "user"
            and resource.user_id.active
            and resource.user_id
        )
        if not resource and not resource_user:
            return Intervals(intervals)
        domain = [("start", "<=", end_dt), ("stop", ">=", start_dt)]
        if resource_user:
            domain += [("partner_ids", "=", resource_user.partner_id.id)]
        all_events = (
            self.env["calendar.event"].with_context(active_test=True).search(domain)
        )
        for event in all_events:
            # Is the event the same one we're currently checking?
            if event.resource_booking_ids.id == analyzed_booking_id:
                continue
            try:
                # Is the event not booking our resource?
                if resource & event.mapped(
                    "resource_booking_ids.combination_id.resource_ids"
                ):
                    raise Busy
                # Special cases when the booked resource is a person.
                # BF change vs upstream: an event marked "free" never blocks,
                # even when the resource user attends it.
                if resource_user and event.show_as == "busy":
                    # Is it an event belonging to the resource?
                    if event.user_id == resource_user:
                        raise Busy
                    # ... or is he invited to this event?
                    for attendee in event.attendee_ids:
                        if (
                            attendee.partner_id == resource_user.partner_id
                            and attendee.state != "declined"
                        ):
                            raise Busy
            except Busy:
                # Add the matched event as a busy interval
                intervals.append(
                    (
                        fields.Datetime.context_timestamp(
                            event, fields.Datetime.to_datetime(event.start)
                        ),
                        fields.Datetime.context_timestamp(
                            event, fields.Datetime.to_datetime(event.stop)
                        ),
                        self.env["resource.calendar.leaves"],
                    )
                )
        return Intervals(intervals)
