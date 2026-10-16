# Symbifox École: timetable (`bf_school_timetable`)

The timetable in a cycle of school days, rooms with no double booking, the family's week in the
portal, and a private calendar link for each child.

## Features

- **Periods and a cycle**: the periods of the day and a cycle of 9 or 10 school days (or a plain
  week). The timetable says "day 3, period 2"; the calendar says which date is which day of the cycle
  and skips holidays and pedagogical days without losing its place (change a day's kind and the
  cycle renumbers; so does a new first day or a new length of the cycle). The day of the cycle is
  computed: nobody types it, not even through an API.
- **Classes**: each course of a group on a day of the cycle, in a period, in a room. A teacher, a room
  or a group is never in two places at once, unless the group splits (ticked on both classes).
- **Periods** of a day never overlap, and adding a teacher to a course is checked against the classes it
  already has (a teacher is never in two places at once, whenever it is set).
- **Import** of the classes from a CSV file as a timetable program exports it (group, subject, day,
  period, room); every line that could not be placed is said, the others go in. A replacement
  ("Replace the classes of the groups in the file") is all or nothing: one line that cannot be placed,
  and nothing is replaced. A file is at most 5 MB and 20,000 lines. A class already in the timetable
  is said, not an error: the same file can be loaded twice.
- **Family portal**: the next seven days of each minor child, and a private calendar address (iCalendar)
  the adult adds to their calendar program. Each adult has their own address, named for the child's
  first name only; replacing it stops that adult's old one and no one else's. Each date comes from
  the published timetable in force on that date: a timetable that replaces another from the 26th is
  read from the 26th, in the portal and in the calendar (which shows 14 days back and 120 ahead).
- **Printable timetable** of a group: the published timetable in force today, else the next one to
  start, else the last one that was; never a draft.

## Security

The office writes the timetable; teachers read it; the staff of a company sees its own company's (the
calendar links included: a link belongs to the company of the school its student is enrolled in).
A calendar address is a bearer secret that carries the child's classes and nothing else; only the adults who
receive the school's notices make one, from their own portal. Whoever holds the address reads the
timetable, so it is **checked at every reading, not only when it is made**: it answers "not found",
like an address that never existed, as soon as the adult no longer receives the notices for the child,
no longer has an active portal account, or the student turns 18 (a student of age is no longer the
family's to follow; a student with no birth date on file is taken for a minor). Not even the office reads
the address itself. The answer carries no session cookie and asks that the address not be sent as a
referrer; the address is in the path of the request, so it is in the web server's own logs: replace it
if those are shared.

## What has not been confirmed

- **EDT** (Index-Éducation) is the program Plurilogic's customers use: its export format was not
  available to check. The import reads a plain CSV by column names; an EDT export saved as CSV with
  those headings works, one in another layout needs its columns renamed.
- A school timetable that changes by week, not by cycle (rotating schedules over months), is
  not modelled: a new timetable for the new period does it.
