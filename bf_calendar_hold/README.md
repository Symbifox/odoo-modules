# BF Calendar Held Slots

When a meeting has to move, hold several candidate slots for it, see them on the
meeting and in the calendar in their own colour, then move the meeting to the
one that is chosen. The other slots are released.

## Why

Before this module, a slot was held by hand: an event titled "RESERVED to move
the meeting with …". Nothing linked it to its meeting, and once the meeting had
moved onto it, the slot stayed on top of the meeting, in Odoo and in Nextcloud.

## How it works

- **Hold slots** (button on the meeting): opens the calendar on the meeting's
  week. Every slot drawn there is held for that meeting.
- A held slot is an ordinary calendar event, linked to its meeting:
  - status *Tentative* (hatched, dashed outline, from `bf_calendar_invite`);
  - *Busy*, so online booking no longer offers that time;
  - the organizer only: nobody is invited, no reminder;
  - its own colour, from the automatic colour rule *Held slots* (`bf_color`),
    which can be changed under the colour rules;
  - a title starting with ↪ and the meeting's name.
- On the meeting, the list of its held slots, with **Choose this slot** and
  **Release**. The same choice is in the slot's popover in the calendar.
- **Choose this slot** moves the meeting there, keeping its duration, agenda,
  attendees and video link, and releases all its held slots. For one
  occurrence of a recurring meeting, only that occurrence moves. Whether the
  attendees get a change notice follows the usual setting of `bf_calendar_invite`.
- Slots are also released when the meeting is cancelled, archived or deleted,
  when the meeting is moved by hand onto one of its slots, and every night once
  a slot's time has passed.
- When the meeting lives in a Nextcloud calendar, slots go to that calendar
  as tentative, busy events, and a released slot is deleted there.
- A slot takes the meeting's privacy: the title of a private meeting stays hidden.
- When Nextcloud recreates a recurring series, the slots of its occurrences are
  linked again to the occurrence with the same original time. An occurrence
  moved onto one of its slots in Nextcloud releases its slots, as it does in Odoo.
- Choosing a slot removes the slots from Odoo before moving the meeting (online
  booking would otherwise see the chosen slot as a conflict) and from Nextcloud
  only once the move has succeeded.
- Only an explicit slot is a slot: copying an event, or creating one on the
  server side, while the calendar is open in "hold slots" mode does not turn it
  into a slot. The mode ends when a button opens another screen. Inside it,
  every new event is a slot, and the banner says so.
- Linking an event to a meeting requires the right to change that meeting,
  whether the slot is created or an existing event is edited.
- Slots in a Google calendar are deleted from Google when a slot is chosen, once
  the meeting has moved (as for Nextcloud).
- A slot whose Nextcloud address is refused is removed from Odoo, and the user is
  told it is still in Nextcloud.

## Requirements

- `calendar_nextcloud_sync` 18.0.2.25.0 or later: the original time of an
  occurrence, which links a slot back to its occurrence when Nextcloud recreates
  a series. The module depends on it even without a Nextcloud calendar.
- `bf_calendar_invite`, for the Tentative status and its hatching.

## Not included

- The guest choosing among the slots through a link.
- A line drawn between a slot and its meeting: the calendar draws none, the ↪
  in the title and the link in the popover stand for it.
- A dedicated look in Symbifox Mobile.

## Version history

| Version | Date | Changes |
|---------|------|---------|
| 18.0.1.0.2 | 2026-10-18 | A slot follows its meeting's privacy when it changes later or when the slot is linked again, not only at creation, Nextcloud included. |
| 18.0.1.0.1 | 2026-10-11 | The meeting's list of held slots is in calendar order and shows times to the minute. |
| 18.0.1.0.0 | 2026-10-11 | First version. |
