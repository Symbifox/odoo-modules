# Symbifox École: child care (`bf_school_childcare`)

School child care, attendance only: who is expected, who came, who left with whom. What child
care costs is billed by another module.

## Features

- **Periods** per school (School form, Child care tab, or Configuration): before school, lunch
  time and after school run on the class days of the school calendar; a day without class runs on
  the open days without class (pedagogical day, a break the office opened). Hours, weekdays,
  levels (preschool and elementary only: Act respecting private education, s. 62.1), places
  (0: no limit), grace minutes after the end.
- **Settings** of the school: booking notice (24 h), free cancellation notice (24 h), minimum age
  to leave alone (0: none), the child care manager who receives the requests.
- **Regular registration**: periods and weekdays between two dates, within a school year. The
  family asks on the portal (a request and an activity for the manager); the manager puts it in
  force after the places are checked on every date from today, and every adult with parental
  authority is emailed. One registration or request at a time per child over a period; a
  registration in force is not edited, it is ended. Occasional bookings it now covers are
  cancelled at no charge.
- **Occasional booking** (portal or office). Refused: a past date, a closed day, a period that does
  not run that day or is not for the child's level, a child who is not a preschool or elementary
  student of the school, a child already registered or booked, a full period. A family books at
  least the school's notice ahead; later, the office books it, marked *late notice*. A
  cancellation later than the free cancellation notice is marked *late* and the place stays due.
  When the calendar closes a day, its bookings are closed.
- **Days without class**: an activity with its places, a minimum and a booking deadline; confirmed
  when the minimum is reached; cancelled, its bookings are cancelled at no charge and the families
  emailed.
- **Absence notices** (portal or office), for a period or the whole day; a whole-day absence
  declared to the school (`bf_school_attendance`) counts too. The place stays due. An open sheet
  marks the child absent at once and says so in its chatter.
- **Daily sheet** for the educator (phone-sized): **Today** opens the sheets of the periods that
  run today with the children expected (registrations and bookings); **Refresh** adds the new
  ones without touching the others; a child present without registration is added as *not
  registered*, and removed with **Remove** when added by mistake. Arrival and departure by buttons
  only, on the school's clock, on today's sheet: a later sheet takes nothing, a past one left open
  is corrected by the manager (no lateness counted, a note in its chatter). The departure window
  offers only the people allowed that day: the family links that may pick up, the authorized
  people valid that day (ID checked the first time, by ticking a box), leaving alone when the
  permission covers the day and the hour, to class before school and at lunch, or someone else
  (name and reason required, reviewed by the manager). Lateness in minutes after the end and the
  grace, on the school's clock. Closing: every child of an after-school period or a day without
  class has left; before school and at lunch, the children still there go to class.
- **Who may pick up a child**: authorized people added or removed by an adult with parental
  authority on the portal (or by the office), with their dates; pick-up restrictions (court order)
  entered by the manager or the Administration, matched by contact or by name (accents, case and
  punctuation ignored, also in the note of "someone else"): the person is never offered and a
  departure with them is refused; the educator sees the name, never the detail nor the document.
  A person added under the name of a restriction raises an activity for the manager, without a
  word to the family. Permission to leave alone: weekdays, from a time, dates, the school's minimum
  age (checked when signed and again at each departure); signed on the portal by an adult with
  parental authority, within a school year the child is enrolled in, or on paper and recorded by
  the office; the exact text accepted and its SHA-256 fingerprint are kept; revoked, never edited.
- **Emails** to every adult with parental authority, in their language, in the school's layout:
  registration confirmed; a person added or removed; leaving alone signed or revoked; activity
  cancelled.
- **Family portal** (**Child care**, adults with parental authority only): the registration, the
  next two weeks (on the schedule, booked, absence noted, full, book, cancel, will be absent), a
  registration request, absence notices, who may pick up the child, leaving alone (sign, revoke),
  the departures of the last 30 days with their time on the school's clock and any lateness.

## Roles

- **Educator** (`group_school_childcare`): implies only the internal user, not school staff:
  reads neither the family links, nor the conduct, nor the registrations, bookings, authorized
  people or restrictions. Works on today's open sheets; what the educator needs about a child of
  the sheet (alert, who may pick up, restriction flag) is computed in sudo on the line.
- **Manager** (`group_school_childcare_manager`): implies the educator; everything above, and
  reopens a closed sheet.
- **Administration** of the school implies the manager.

Reserved fields (states, evidence, timestamps, who did what) are refused at creation and at
writing when a user who is not the system forges them; the defaults the web client sends with a
new record pass. Every portal route checks the adult's parental authority over the child before
reading anything in sudo, and every id received from a form against that child.

## For a billing module

Extension points, each a method that does nothing here:
`bf.school.childcare.registration._school_on_activated()`,
`bf.school.childcare.booking._school_on_booked()` and `_school_on_cancelled()`,
`bf.school.childcare.sheet._school_on_closed()`,
`bf.school.childcare.sheet.line._school_on_departure()`, `_school_on_walk_in()` and
`_school_on_removed()` (a line added by mistake, about to be removed).

Fields to read: `registration.periods_per_week`, `registration.line_ids` (period and weekdays);
`booking.state`, `late_notice`, `cancel_late`, `activity_id`, `channel`; `sheet.line.origin`
(`registration`, `booking`, `walk_in`), `presence`, `arrived_on`, `departed_on`, `late_minutes`,
`registration_id`, `booking_id`; `slot.kind`, `capacity`, `grace_minutes`.

## What has not been confirmed

- No pilot school yet: the rules follow what 18 private schools publish.
- Who may pick up a child is not printed on paper; no SMS.
- An early dismissal of the calendar does not move the start of the after-school period.
- The places of a regular registration are checked when it is put in force, not again when a
  period's capacity is lowered afterwards.
- A booking on a date its period no longer runs (a class day turned pedagogical, a weekday
  unticked, a period archived) is closed, nothing due.
- The people allowed or not allowed to pick up a child, and the permissions to leave alone,
  belong to the company of the student's school, not to the student card.
