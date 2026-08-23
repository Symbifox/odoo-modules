# Link activities to existing calendar events (`bf_activity_calendar_link`)

Attach an **existing** calendar event to a scheduled activity, instead of letting
Odoo create a second one.

## Why

Odoo's "Meeting" activity type creates a fresh `calendar.event` when you schedule
it. But the meeting often already exists — it came from a booking page, from a
CalDAV sync, from an invitation someone else sent. Scheduling the activity then
produces a duplicate: two entries in the calendar, two reminders, and no link
between the activity and the meeting that will actually happen.

This module lets the activity point at the event that already exists.

## What it provides

- A `calendar_event_id` field on `mail.activity`, with a picker limited to events
  the user can see.
- Marking the activity done closes it against the linked event rather than
  against a phantom one.
- The link is optional: an activity with no event behaves exactly as before.

## Requirements

Odoo 18 Community, `calendar`, and `calendar_nextcloud_sync` (published in this
repository) for the sync-aware behaviour.

## License

LGPL-3.
