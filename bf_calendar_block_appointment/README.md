# BF Calendar Work Blocks - Online Booking

A **meeting slot** (a work block whose type *Opens online booking*, from
`bf_calendar_block`) adds bookable time for the person who sets it (or accepts
it, when a colleague set it for them), on top of their working hours and of the
hours of the booking type: book Saturday 10:00 to 12:00 as a meeting slot, and
clients can book you then. Busy events that overlap
the slot close that part of it, as anywhere else. Meeting slots are always shown
as available. Outside online booking (timesheets, leaves, payroll), a meeting
slot is not working time.

Installed automatically with `bf_calendar_block`, `resource_booking` and `bf_appointment`; the
*Meeting slot* type of `bf_calendar_block` opens booking.

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 18.0.1.0.1 | 2026-10-18 | A meeting slot opens only the calendar of the person who set it or accepted it. |
| 18.0.1.0.0 | 2026-10-14 | Meeting slots open online booking |

## License

LGPL-3.0
