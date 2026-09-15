# NFC tags: meeting room (`bf_nfc_room`)

A tag on the room door. Bring the phone close:

- **Free**: "Take 30 min", "Take 1 h", bounded by the next booking. The booking
  is created in the calendar, already confirmed.
- **Booked by you and about to start**: "I'm here" confirms.
- **Taken by you**: "Release the room" frees it right away.
- **Taken by someone else**: the screen says by whom and until when.

A booking nobody confirms within ten minutes (per-room setting) loses its room:
the event stays in the calendar, the room becomes free again, and a note says so
on the event. Two bookings of the same room cannot overlap, whether they come
from a tag or from the calendar.

## Design rules

- **Tell first, act on a button.** The first tap never takes anything: taking a
  room by mistake while walking past would block a colleague's room.
- **State is read again when the button is pressed.** Between the question and
  the choice someone may have booked through the calendar; the overlap
  constraint decides, and the refusal says so.
- **A booking is an ordinary calendar event with a room**, not a parallel model:
  what is booked at the door shows in everyone's calendar, and the other way
  round.
- No signed tag and no offline tap: a room is taken on site, now.

## Setup

*Tags → Rooms*: create the room, set the offered durations and the confirmation
delay, then *Create the door tag* and engrave it from the app. In the calendar,
an event books a room through its **Room** field.

The *Meeting room* template creates rooms and their door tags from a list of lines.

## License

Business Source License 1.1, see [LICENSE](LICENSE).
