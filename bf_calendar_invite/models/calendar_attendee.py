"""Stand core's own "date updated" notice down, now that we send one.

Core sends `calendar.calendar_template_meeting_changedate` from inside
`calendar.event.write()`, with `force_send=True`, whenever `start` is in the
values. It is the only notice Odoo sends by itself, and it is the one member of
this family that was never dressed: no brand, no before-and-after, no mention of
the room or the joining link, and no way to decide against sending it.

Leaving it in place while this module sends its own would write to every guest
twice for the same move, from two different templates, one of which contradicts
the other by being silent about everything but the start time.

⚠️ Only that template is intercepted. The invitation and the reminder go out
untouched: they are core's job, they work, and a broad "no calendar mail"
switch here would be a second, invisible copy of `calendar.block_mail`.
"""

import logging

from odoo import models

_logger = logging.getLogger(__name__)


class CalendarAttendee(models.Model):
    _inherit = "calendar.attendee"

    def _send_mail_to_attendees(self, mail_template, force_send=False):
        changedate = self.env.ref(
            "calendar.calendar_template_meeting_changedate",
            raise_if_not_found=False,
        )
        if (
            changedate
            and mail_template
            and mail_template.id == changedate.id
            and not self.env.context.get("bf_allow_core_changedate")
        ):
            # Logged rather than dropped in silence. The day someone wonders
            # why a move produced no mail at all, the answer has to be findable
            # from the log of the request that made the move.
            _logger.info(
                "bf_calendar_invite: core's change notice stood down for "
                "attendees %s; bf_calendar_invite sends its own.", self.ids,
            )
            return False
        return super()._send_mail_to_attendees(mail_template, force_send=force_send)
