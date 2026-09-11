"""Booking availability: honour show_as='free' on attended events.

Upstream OCA ``resource_booking`` counts a person-resource as busy whenever
their partner attends a calendar event (state != declined), regardless of the
event's ``show_as`` flag. Google-synced all-day events ("Bureau", working
location, "Occupé(e)") arrive with show_as='free' and the synced user as
attendee, stored 08:00→18:00 UTC = 4h→14h Montréal — so every workday morning
was silently removed from the public slot picker (reported by a tenant,
2026-07-03: the "Questionnaire d'audit initial" type only ever offered
14h/14h30/15h starts Mon-Thu and nothing on Fridays).

Reimplements ``_calendar_event_busy_intervals`` with two changes:

1. the attendee branch only blocks when the event is marked busy, matching the
   owner branch and the free/busy semantics of mainstream booking tools;
2. a booking whose type carries ``slot_capacity > 1`` only closes the slot once
   that many bookings sit on it. Below the cap the slot stays on offer, which
   is what an open house or a group intake needs. With the default capacity of
   1, the behaviour is bit-for-bit the historical one.
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
                    if not self._bf_slot_has_room(
                        event, event.resource_booking_ids, resource,
                        analyzed_booking_id,
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

    @api.model
    def _bf_slot_has_room(self, event, bookings, resource, analyzed_booking_id):
        """Reste-t-il de la place sur le créneau que cet événement occupe?

        Faux (donc « occupé ») dans le cas usuel, où une réservation retient le
        créneau pour elle seule. Vrai tant que le plafond du type n'est pas
        atteint, quand ce plafond est déclaré au-delà de 1.

        Le plafond est lu sur le TYPE, et le plus petit gagne quand plusieurs
        types se rencontrent sur un même événement : une place promise par un
        type ne se prend pas sur le dos d'un autre.

        ⚠️ Le comptage est fait en `sudo()`. Un visiteur anonyme ne peut pas
        lire les réservations des autres, et sans ça il verrait toujours de la
        place. Rien de ce qui est lu ne sort d'ici : seul un nombre est comparé
        au plafond.
        """
        types = bookings.mapped("type_id")
        capacities = [t.slot_capacity or 1 for t in types]
        capacity = min(capacities) if capacities else 1
        if capacity <= 1:
            return False
        domain = [
            ("type_id", "in", types.ids),
            ("state", "!=", "canceled"),
            ("start", "<", event.stop),
            ("stop", ">", event.start),
            ("combination_id.resource_ids", "in", resource.ids),
        ]
        if analyzed_booking_id and analyzed_booking_id > 0:
            domain.append(("id", "!=", analyzed_booking_id))
        taken = self.env["resource.booking"].sudo().search_count(domain)
        return taken < capacity
