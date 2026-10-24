# BF Working Hours (`bf_work_hours`)

Each person's real working time, reconstructed from their own traces and shown
to them, and to the colleagues they name, in **Odoo 18 Community**. Off until the
person turns it on.

A timesheet says where effort went. It does not say how long someone was
actually at work: hours entered late are estimates, and work done with several
assistants or agents running in parallel can be entered one stretch after the
other. This module rebuilds, for the person, the stretches of the day where a
trace proves they were working, and shows the result beside what they entered.
Two numbers for two uses: neither replaces the other.

## Off by default, turned on by the person

Reading activity traces to evaluate how someone works is *profiling* under
Quebec's private sector privacy act (section 8.1 names "work performance"), and
the Commission d'accès à l'information reads it as **off by default, switched on
by the person concerned**. So:

- Nothing is collected until the person reads the notice (what is read, who
  sees it, how long it is kept, how to turn it off) and turns the feature on
  **themselves**. The first time they open *Working Time*, the consent screen
  comes first: *Turn on*, or *Go on without it*, two buttons of the same size;
  either answer is final for that screen, which does not come back. Later, the
  same screen opens from *Working Time > My choices* or the *Working time* tab
  of their preferences. The notice the server wrote is kept with the
  activation, word for word: the screen lays it out, it never writes its own.
- **In their own web session only**: not through an API key (an agent or a
  script holding it), not while someone impersonates them. The same holds for
  turning it off, sharing and revoking.
- Three switches: **Propose my hours** (the reconstructed hours), **My
  indicators** (span of the day, longest stretch, meetings, time outside the
  working schedule) and **My earlier days** (see below). The last two need the
  first.
- **Nobody else can turn it on, off, or read it in Odoo's screens and RPC**,
  managers and administrators included: global record rules bind every user.
  A system administrator who changes the security settings or runs code on the
  server could still reach the data, as with any data in Odoo; the notice says so.
- Turning *Propose my hours* off **destroys** the traces and the days and ends
  the shares. The activation itself is kept as evidence, marked withdrawn.
- Days before the activation are read only if the person chooses **My
  earlier days**, a consent of its own with its own notice: reading again traces
  recorded for other purposes is a new use (section 12). The person picks how
  far back: the last 30 days, or all the history kept (as far as a day is kept,
  `bf_work_hours.day_retention_days`). It can be chosen on the first screen
  with the hours, or later in *My choices*, and turned off alone, which destroys
  the days from before the activation of the hours. A job takes the earlier
  days in a slice at a time, starting at once (*Working time: take in earlier
  days*).

## What counts as a trace

Only traces that prove a hand. The chatter and field changes do **not** count:
an agent or a script writing with someone's API key signs them with that
person's name.

| Source | Module | What is kept |
|---|---|---|
| Browser activity | this module | Whether the person used the keyboard or the mouse in Odoo during the last two minutes, reported once a minute by a small service of their own web session (Odoo itself only reports presence when it changes). Never from an API key, never under an impersonation. Not what was typed, not which page. |
| Calendar | this module | Meetings with at least one other attendee, busy, under 8 hours, that the person accepted themselves (or that a calendar synchronization accepted for them). Each answer is stamped when it is written (by the person, by Odoo in a job, by someone else) with the hours of the meeting at that moment, and again when the line is handed to someone else: an answer written by a colleague does not count, and a meeting a colleague moves counts only for the hours that were accepted. A meeting the person moves themselves carries the answer to its new hours. Answers given before the installation are stamped at installation from their last writer. |
| Your own tools | this module (`bf_work_push`) | Moments a local tool of the person reports for them, under their own identity: never from before the start of reading, 48 hours back at most, or further with *My earlier days* (within the retention of raw traces). The tool asks how far back it may go (`bf_work_push_window`). |
| Gen prompts | `bf_work_hours_claude_chat` | When a prompt was sent, not its text. |
| Notes | `bf_work_hours_notes` | When a note was created. |
| Emails sent from the mail client | `bf_work_hours_email` | When it was sent (read back from the Sent folder). |
| Meeting reports | `bf_work_hours_meeting` | Start and length of the meetings where the person is marked present. |
| Timer | `bf_work_hours_timer` | Start and end of each stretch the timer ran, kept when it stops (with the mobile timer, ended at the real moment of a pause made offline on the phone). A running timer does not count yet; a stretch of more than 12 hours is a forgotten timer and is left out. |

*My earlier days* reads again the calendar, the meeting reports, Gen prompts,
notes, emails and the person's own tools. Browser activity and the timer are
captured live, only once the hours are on: there is none from before, and a
stretch that started before the activation counts only from it.

## How the hours are rebuilt

1. A one-off trace (a prompt, a note) is worth the few minutes **before** it:
   the time it took to produce it (2 minutes; 5 for an email).
2. All stretches are merged: **a minute counts once**, even when two traces, or
   two agents, fill it.
3. Two stretches closer than the **idle gap** (15 minutes by default,
   `bf_work_hours.gap_minutes`) become one.
4. Days are cut at the person's local midnight.

The idle gap is a choice, not a measure: moving it from 5 to 30 minutes can
change a week by a third. Every day therefore shows a **low bound** (5 minutes)
and a **high bound** (30 minutes) beside the figure, and *What it rests on*
gives the hours each source would give alone. Time away from the screen is not
seen.

## Who sees what

- **The person**: *Working Time > My working time* (list, graph, pivot).
- **A colleague the person names**: *My shares* lets the person share their
  days (never the raw traces) with a named internal user, and revoke it at any
  time; revoking cuts access to the whole history at once. A shared day shows what
  the person sees on it: the hours and their bounds, what they rest on, source by
  source, the hours entered in timesheets (with the timesheet bridge), and the indicators if
  they turned them on.
- **Nobody else.** No manager view, no score, no ranking, and nothing is
  decided automatically from these figures. The employer's record of working
  hours remains the timesheet the person enters and approves.

## Retention

Raw traces: 60 days (`bf_work_hours.signal_retention_days`). Days: 400 days
(`bf_work_hours.day_retention_days`). An hourly job purges first, then
reconstructs the last 7 days of every person who turned the feature on, one
person at a time (a failure for one does not stop the others). Indicators are
computed from the day they were turned on, and over the earlier days taken in
(the days before the activation of the hours); the days between the activation
of the hours and that of the indicators get none, as without earlier days.
*Recompute my last days* redoes the same 7 days at once and says what it did:
how many days, and from when the traces are read.

With **Privacy Consent** installed, each activation also appears in the privacy
register as the person's consent (`bf_work_hours_privacy`): the people in charge
of privacy see that, and when, never the hours.

## Before you deploy

A privacy impact assessment is required before deploying a system that
processes personal information (section 3.3), and the people concerned must be
told before anything is collected (section 8.1): the activation notice does the
second for each person, not the first for the organization.

## License

LGPL-3. Les services de consultation Blue Fox, Inc., https://symbifox.com
