# NFC tags: meeting attendance (`bf_nfc_meeting`)

A tag on the meeting room table or door. When arriving, bring the phone close:
you are marked *Present* in the attendance of the meeting in progress, the list
the minutes are built from.

- **Tag without a record**: the meeting in progress the person is invited to
  (meeting guests, attendees of its calendar event, or organizer). If there are
  two, the screen asks which one.
- **Tag on a meeting**: that one, invited or not. Someone arriving without an
  invitation is still recorded: the minutes say who was there.

Attendance is recorded from thirty minutes before the start to fifteen minutes
after the planned end (one hour when no duration is set). An *Absent* or
*Excused* line becomes *Present*. A signed tag records no attendance: it acts for
a designated account, not for the person.

Installs itself when both `bf_nfc` and `bf_meeting` are present.

## Changelog

- **18.0.1.0.2**: the two messages shown on the phone (no meeting in progress,
  several meetings to choose from) are reworded in gender-neutral French.

## License

Business Source License 1.1, see [LICENSE](LICENSE).
