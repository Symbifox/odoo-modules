# BF Calendar Work Blocks - Meetings

Work blocks (focus, production, meeting slot, catch-up) ask for neither an
agenda nor minutes, and stay off the meetings dashboard.

## Why

A work block is time booked for oneself, not a meeting. Yet each one asked
for an agenda and minutes, and showed on the meetings dashboard, until both
were ticked off by hand on every block.

## How it works

- At installation, the four block types of `bf_calendar_block` get
  *No formal agenda* and *Exclude from dashboard* (from `bf_meeting`).
  A box already ticked is left alone; nothing is unticked.
- `bf_meeting` copies these boxes from the tag to the event: every new block
  of these types is exempted, whether created from the calendar, the form, a
  phone or by RPC.
- Upcoming blocks of these types are exempted at installation; past ones are
  left as they are.

Install it where both `bf_calendar_block` and `bf_meeting` are installed.

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 18.0.1.0.0 | 2026-10-31 | Four block types exempted from agenda, minutes and dashboard; upcoming blocks aligned |

## License

LGPL-3.0
