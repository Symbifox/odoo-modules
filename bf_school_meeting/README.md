# Symbifox École: parent-teacher meetings (`bf_school_meeting`)

Parent-teacher meetings, booked by the families on the portal.

## Features

- **Sessions**: one per report card (or any occasion), for chosen groups of the
  current year, with a slot length (10 minutes by default), a place and a booking
  period.
- The school office enters each **teacher's hours**; **Generate the slots** cuts them
  into slots. Regenerating after a change never touches a booked slot.
- On the family portal (**Parent-teacher meetings**), each child shows the teachers of
  their groups taking part in the session, with their free slots. One click books,
  one click cancels, until booking closes.
- Rules, all checked on the server:
  - one meeting per child and teacher;
  - the teacher must teach that child in a group of the session;
  - an adult never holds two slots at the same time, so their meetings follow one
    another;
  - two parents clicking the same slot at once: a conditional database update gives
    it to one of them, the other is asked to choose again;
  - only the adult who booked cancels.
- A confirmation email with the time, the teacher and the place.
- Every time shown to a family is in the school's time zone (a portal or anonymous
  visitor usually has none, and Odoo would otherwise show UTC).
- Teachers see **My schedule**: their own slots with the children's names.

## Security

The office manages sessions, hours and slots. A teacher reads their own slots only.
Families have no access right; the portal reads through the adult's own guardian
links. An error message travels in the session, never in the URL.

## What has not been confirmed

- The simultaneous-click guard is a database condition; no test drives two real
  concurrent transactions.
- No video meetings, no waiting list for a full teacher, no reminder the day before.
- Teachers cannot enter their own hours yet: the office does.
