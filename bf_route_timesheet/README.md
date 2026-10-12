# Symbifox Work Routes: Time Sheets (`bf_route_timesheet`)

A finished route day logs the worker's time, from the start of the day to its
end, on the route's project. Installs itself when Work routes (`bf_route`) and
Timesheets (`hr_timesheet`) are both installed.

> **Not legal advice.** The sections of the *Act respecting labour standards* cited
> below are given as the reason for the module, not as a ruling on your case. Check them
> with your adviser.

## Why

The phone already timestamps when the worker starts and finishes the day.
Quebec's *Act respecting labour standards* (s. 57) counts the travel the
employer requires as work time: the drive between customers is paid time, and
once an app timestamps the day, the hours are no longer "uncontrollable"
(s. 54). Logging them from the day avoids typing them twice.

## Features

- **Project of the route** (*Routes › Routes*, next to the vehicle): the
  project and, optionally, the task the time goes to. *Settings › Work routes ›
  Time sheets* gives a default project for the routes that name none.
- **At the end of the day** (the worker taps *Finish the day*): one time sheet
  line for the worker's employee, date of the day, hours between *Start the
  day* and *Finish the day*, description with the route and the stops done.
- **No project, no time**: without a project on the route or in the settings,
  nothing is logged. A worker without an employee in the company gets a note on
  the day; the day still finishes.
- **A day left open is not logged**: more than 14 hours between the start and
  the end (finished the next morning) gives a note on the day instead; the
  office enters the time by hand.
- **A start sent late is not trusted**: the hours are logged only when the
  server received the start within 15 minutes of the time the phone gave it.
  Otherwise (a phone clock set back, a past day started after the fact), a
  note on the day says so. The start and the end remain the worker's taps:
  a day started from home at 5 a.m. is logged as such, up to 14 hours.
- The day shows the hours logged (button *Hours logged*, for time sheet users).

## Access

The line is created by the module (sudo) for the worker's own employee only.
The link between a time sheet line and a route day can only be set by the
module. Reading the lines follows Odoo's time sheet rules.

## Known limits

- The day runs from *Start the day* to *Finish the day* as tapped on the phone.
  The drive from home to the depot (the "normal commute") is not split out:
  start the day at the depot.
- One line per day, logged once. Correcting it is done on the time sheet.
- The meal period is not taken out: the Act (s. 79) gives 30 minutes, unpaid
  after 5 hours of work unless the employee cannot leave their post. Adjust the
  line when it applies.

## License

Business Source License 1.1, see `LICENSE`.
