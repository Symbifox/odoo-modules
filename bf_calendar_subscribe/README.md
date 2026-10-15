# BF Calendar Subscriptions

Show a calendar published elsewhere (Outlook, Microsoft 365, Google, Apple,
another Nextcloud) in the Odoo calendar, read only, in its own colour, and keep
its busy times out of online booking.

## How it works

- *Calendar > External calendars*: paste the iCalendar address the other
  calendar publishes (`https://` or `webcal://`). In Outlook: *Settings >
  Calendar > Shared calendars > Publish a calendar*, then copy the ICS link.
- The calendar is read every hour by default (15 minutes at least), with
  conditional requests. Recurring events are expanded over the chosen window
  (7 days back, 180 days ahead by default), with their exclusions and moved
  occurrences; cancelled events are skipped.
- Imported events belong to you, are **private** by default (colleagues and
  sharing links see *Busy*), have no reminder, no agenda, and are read only:
  they are changed in the other calendar, never here. Only busy events are
  imported unless you ask for free ones too. Locations are imported on request.
- Busy imported events close online booking slots (`resource_booking`,
  `bf_appointment`).
- Imported events never leave Odoo: not to Nextcloud, not as invitations.
- The address is visible to you only (it gives read access to that calendar).

## Safety

The address is typed by a user, so reading it is guarded: https only, port 443,
the host is resolved once and pinned, any non-public address is refused
(loopback, private networks, link-local, cloud metadata), each redirection is
checked again, size is capped at 5 MB, the connection at 5 seconds and each wait for data at 20 seconds.

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 18.0.1.0.1 | 2026-10-15 | The example in the subscription name is now generic |
| 18.0.1.0.0 | 2026-10-14 | External calendar subscriptions (lot 6) |

## License

LGPL-3.0
