# Symbifox École: school calendar (`bf_school_calendar`)

The calendar of each school and school year: what kind of day each date is, and
whether child care opens.

## Features

- **One day per school and date**: class day, pedagogical day, school break,
  holiday (closed) or emergency closure, with an early dismissal time when classes
  end earlier than usual, and a note. The school year is found from the date.
- **Child care open**: yes by default on class and pedagogical days, no on breaks,
  holidays and closures. The office may open child care on a break (spring break
  camp) or close it on any day.
- **Make the class days**: a button on each school year (school form, School years
  tab) adds a class day for every weekday between the year's dates, without touching
  the days already set. Pressed again after a change of dates, it only fills the gaps.
- **Views**: an editable list, a month calendar coloured by kind of day, filters by
  kind, by child care open or closed and by early dismissal, grouping by school, year,
  kind and month.
- **Access**: every staff member reads the calendar; the Administration keeps it.
  Multi-company rule.

## For the other school modules

- `bf.school._calendar_today()` and `_calendar_now()`: today and now on the school's
  clock (the company's time zone, else `America/Toronto`), never the server's UTC.
- `bf.school._calendar_at(date, hour)`: the UTC datetime of a time of day at school,
  and `_calendar_local(datetime)` for the reverse.
- `bf.school._calendar_day(date)`, `_calendar_childcare_open(date)`,
  `_calendar_is_class_day(date)`: read in sudo. A date the calendar does not hold is
  closed.
- `bf.school.calendar.day._school_childcare_closed()` is called when child care stops
  opening on days that were open (kind changed, box unticked, day removed). It does
  nothing here; a module that books child care closes what was booked.
- `bf.school.calendar.day._school_calendar_changed(before)` is called when days change
  kind, date or school; `before` holds their (school, date) before the change. It does
  nothing here; a module that books on the calendar checks its bookings.
- A school year whose dates change computes the year of its days again: a day no year
  covers any more is refused.

## What has not been confirmed

- The timetable of the school follows this calendar through `bf_school_timetable_calendar`,
  which installs by itself with the timetable; exam days stay in the timetable.
- Statutory holidays are not filled in automatically: the office marks them.
