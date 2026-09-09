import logging
from email.utils import parseaddr

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    @api.model
    def _cron_cleanup_orphan_booking_events(self):
        """Unlink past calendar.event records orphaned from a resource.booking.

        OCA action_unschedule already unlinks the meeting on a normal cancel,
        but custom cleanup scripts (`--cleanup` flags), direct DB ops, or
        future regressions can leave the calendar.event behind. The orphan
        keeps blocking slots in resource_booking_combination._get_intervals
        because it still belongs to the resource user's calendar.

        We target events that:
          - have the BF appointment naming convention "RDV - <name>"
          - have NO active or archived resource.booking pointing back to them
          - have already started (so we never touch in-flight bookings)
        """
        cutoff = fields.Datetime.now()
        candidates = self.search([
            ("name", "=like", "RDV - %"),
            ("start", "<", cutoff),
            ("resource_booking_ids", "=", False),
        ])
        # Skip events still linked from an *active* booking, that should
        # never happen given the inverse one2many is empty above, but it's a
        # cheap belt-and-suspenders against active_test edge cases.
        # Archived/cancelled booking references are fine: meeting_id is
        # ondelete=set null, so the booking record stays intact when we
        # unlink its old meeting.
        if candidates:
            still_active = self.env["resource.booking"].sudo().search([
                ("meeting_id", "in", candidates.ids),
            ]).mapped("meeting_id")
            to_unlink = candidates - still_active
            if to_unlink:
                _logger.info(
                    "Unlinking %d orphan calendar.event(s) from cancelled bookings",
                    len(to_unlink),
                )
                to_unlink.with_context(
                    no_mail_to_attendees=True,
                    tracking_disable=True,
                    mail_notrack=True,
                ).unlink()

    def _track_subtype(self, init_values):
        """Suppress tracking notifications on events linked to a booking.

        Resource bookings drive their own branded confirmation/reminder
        emails. Stock Odoo otherwise fires a "Date mise à jour" notification
        on every reschedule, AND any orphan calendar.event left behind by
        cancel/rebook cycles (with the booker still in attendee_ids) keeps
        firing them forever. The suppression context set by
        ResourceBooking._sync_meeting only covers the sync path; this guard
        catches every write to a booking-linked event.
        """
        if self.resource_booking_ids:
            return False
        return super()._track_subtype(init_values)

    def _get_ics_file(self):
        """Enrich stock ICS so Gmail/Outlook render it as a real invite.

        Stock Odoo emits a VCALENDAR with no METHOD, bare ATTENDEE:MAILTO
        lines, and no ORGANIZER CN. Mail clients then treat the .ics as a
        plain file attachment instead of an invitation (no RSVP buttons,
        no "Add to calendar"). We rewrite each ICS to add METHOD:REQUEST,
        decorate ATTENDEE/ORGANIZER with CN/CUTYPE/PARTSTAT/ROLE/RSVP,
        and (for events with a videocall) inject LOCATION/URL/DESCRIPTION.
        """
        result = super()._get_ics_file()
        try:
            import vobject
        except ImportError:
            return result

        for event in self:
            ics_data = result.get(event.id)
            if not ics_data:
                continue
            try:
                cal = vobject.readOne(ics_data.decode("utf-8"))

                if not hasattr(cal, "method"):
                    cal.add("method").value = "REQUEST"

                vevent = cal.vevent

                organizer_partner = event.user_id.partner_id or event.partner_id
                # ⚠️ `partner.email` is not guaranteed to hold a BARE address,
                # and `"mailto:" + <whatever is in the field>` is what made the
                # line invalid in production: a partner carrying a FORMATTED
                # address (`"A display name" <mailbox@example.com>`) made every
                # ICS it organised go out as
                # `ORGANIZER:mailto:"A display name" <…>`. A `mailto:` URI takes
                # an address and nothing else (RFC 6068), and a client that
                # rejects the URI rejects the whole VEVENT. Parsed, not
                # repaired: the field's value may well be wanted for what it is,
                # and an ICS generator is not the place to rule on that.
                _cn, organizer_email = parseaddr(
                    (organizer_partner.email or "") if organizer_partner else ""
                )
                if organizer_email:
                    # Replace any existing organizer line so we control params
                    if hasattr(vevent, "organizer"):
                        del vevent.contents["organizer"]
                    organizer = vevent.add("organizer")
                    organizer.value = "mailto:" + organizer_email
                    display = organizer_partner.name or _cn
                    if display:
                        organizer.params["CN"] = [display.replace('"', "'")]

                # Replace plain ATTENDEE:MAILTO lines with fully-parameterized
                # ATTENDEE entries that carry CN / role / RSVP — required for
                # email clients to render the invite as actionable.
                if "attendee" in vevent.contents:
                    del vevent.contents["attendee"]
                for attendee in event.attendee_ids:
                    if not attendee.email:
                        continue
                    att = vevent.add("attendee")
                    att.value = "mailto:" + attendee.email
                    if attendee.partner_id and attendee.partner_id.name:
                        att.params["CN"] = [
                            attendee.partner_id.name.replace('"', "'")
                        ]
                    att.params["CUTYPE"] = ["INDIVIDUAL"]
                    att.params["ROLE"] = ["REQ-PARTICIPANT"]
                    state = attendee.state or "needsAction"
                    partstat_map = {
                        "needsAction": "NEEDS-ACTION",
                        "tentative": "TENTATIVE",
                        "declined": "DECLINED",
                        "accepted": "ACCEPTED",
                    }
                    att.params["PARTSTAT"] = [partstat_map.get(state, "NEEDS-ACTION")]
                    att.params["RSVP"] = ["TRUE"]

                if event.videocall_location:
                    if hasattr(vevent, "location"):
                        vevent.location.value = event.videocall_location
                    else:
                        vevent.add("location").value = event.videocall_location
                    if not hasattr(vevent, "url"):
                        vevent.add("url").value = event.videocall_location
                    desc = (
                        vevent.description.value
                        if hasattr(vevent, "description")
                        else ""
                    ) or ""
                    if event.videocall_location not in desc:
                        video_line = (
                            f"\n\nVidéoconférence : "
                            f"{event.videocall_location}"
                        )
                        desc = desc.rstrip() + video_line
                        if hasattr(vevent, "description"):
                            vevent.description.value = desc
                        else:
                            vevent.add("description").value = desc

                result[event.id] = cal.serialize().encode("utf-8")
            except Exception as e:
                _logger.warning(
                    "Failed to enrich ICS for event %d: %s", event.id, e
                )

        return result
