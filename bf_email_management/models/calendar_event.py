"""Track the source iMIP UID on events auto-created from email invitations.

``bf.email._maybe_ingest_calendar_invite`` stores the incoming VEVENT ``UID``
here so a later reschedule (same UID, higher SEQUENCE, arriving as a fresh
email) updates the existing tentative event instead of duplicating it, and a
``METHOD:CANCEL`` can locate and remove it.

Kept separate from ``calendar_nextcloud_sync``'s ``x_nc_uid`` (the Odoo/NC
CalDAV identity): that one is generated for our own outbound sync, while this
one is the *external* organizer's UID.
"""

from odoo import fields, models


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    x_imip_uid = fields.Char(
        string="iMIP UID",
        index=True,
        copy=False,
        help="UID of the calendar invitation (text/calendar part) this event "
        "was auto-created from by the email module. Used to de-duplicate "
        "reschedules and to honour cancellations.",
    )
