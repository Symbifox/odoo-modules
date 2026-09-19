# Symbifox — Mobile calendar

A read-and-write REST surface (`/bf_calendar/mobile/v1`) that gives a mobile
client a **Calendar** tab (day and week grid) and a **Tasks** tab, carrying the
things CalDAV does not transport: the meeting agenda, the minutes, the RSVP,
and the two reminder gestures — snooze and dismiss.

It adds no view and no menu. It is a surface: the phone app is the front end.

## Why a surface rather than plain CalDAV

A CalDAV client shows the events of the calendars it is subscribed to, and
nothing else. It will not show a meeting agenda, it will not show minutes, and
it cannot acknowledge a reminder in a way the server can read back. Snoozing
and dismissing therefore travel over this surface rather than over the `.ics`
file, and a phone that is signed in sees every event it is on, whichever
calendar that event lives in, rather than only the calendars it happens to
have subscribed to.

## Endpoints

All of them are `auth="public"` and guarded by the app's bearer token; only
`/ping` answers without one.

| Route | Method | Purpose |
|---|---|---|
| `/ping` | GET | capability probe: version, `enabled` |
| `/config` | GET | account time zone, offered snooze presets, capabilities |
| `/events?from=&to=` | GET | the window, for the grid |
| `/event?id=&key=` | GET | one event, with agenda and minutes |
| `/snooze` | POST | `{event_id\|key, minutes}` |
| `/dismiss` | POST | `{event_id\|key}` |
| `/rsvp` | POST | `{event_id\|key, state}` |
| `/calendars` | GET | the calendars this person may write to |
| `/event/create` | POST | create a meeting |
| `/event/flags` | POST | set the two meeting-module exclusions |
| `/partners?q=` | GET | who to invite: readable contacts, name or email, 2 characters minimum |
| `/event/attendees` | POST | `{event_id\|key, add, remove, notify}` — `notify` off by default |
| `/tasks?from=&to=&undated=` | GET | three buckets: overdue, in-window, undated |
| `/tasks/search?q=&limit=` | GET | my open tasks, any deadline: each word against title, project, customer or tag; a bare number finds one by id |
| `/task?id=` | GET | one task by id |
| `/task_counts?from=&to=&tz=` | GET | a per-day count for the grid badge, days computed in `tz` |
| `/task/options` | GET | projects, stages, tags, states and priorities for the pickers |
| `/task/write` | POST | edit a whitelisted set of fields |
| `/task/done` | POST | close or reopen |
| `/task/create` | POST | create a task |

The bearer token is the device token of `bf_sms_archive` or of
`bf_email_management`, whichever the app already holds. Recognising both is
deliberate: the two halves of the app are independent, and depending on one
would lock the other out.

## Four things not to undo

1. **The stable occurrence key.** A CalDAV synchroniser that re-imports an
   `.ics` carrying an `RRULE` can delete a recurrence and recreate its
   occurrences with fresh ids. Every event therefore ships with the stable
   `key` of its reminder acknowledgement, and every write route accepts that
   key when the id has gone stale.

2. **RSVP does not post under "Invitation".** Odoo's `do_accept` posts with
   `calendar.subtype_invitation`, which is not an internal subtype: on a
   meeting a customer follows, accepting from a phone would have written to
   them. This module writes the state and logs an internal note instead.

3. **No invitation of this module's own leaves without being asked for.**
   `notify` on `/event/attendees` is the single door through which one can go
   out, and it is closed by default. (Editing a task still goes through Odoo's
   own tracking, which may notify that task's followers — exactly as it does
   from the desktop.)

4. **Tasks do not go on the grid.** A working calendar holds a handful of
   meetings a day and can hold ten times as many deadlines; drawing them on
   the same grid buries the meetings. The grid receives one count per day, and
   the Tasks tab carries the list.

## Times and colours

Instants leave in UTC with an explicit `Z` and are rendered by the phone in its
own zone — formatting server-side would pick one zone for a person who may not
be in it. The day buckets behind the grid badge are the exception: they are
computed in the IANA zone the app supplies, falling back to the account's, and
the response always says which one was used.

Colours are computed with Odoo's own rule (`((key - 1) % 55) + 1` over the
complete `$o-colors` list) so the grid on the phone paints exactly what the
Calendar view paints on the desktop, plus the 55 %-white mix the stylesheet
applies.

## Out of scope, on purpose

Verbatim transcripts, review notes and structured notes never leave the
server. A phone that is lost must not carry the transcript of a client
meeting; only the summary and the decisions travel.

## Requirements

- Odoo 18 with `calendar` and `project`
- `bf_email_management` — it carries the two reminder verbs (`bf_snooze`,
  `bf_dismiss`) and the durable acknowledgement that gives an occurrence a
  stable key. Without it there is no round trip, so the dependency is real and
  not a shortcut.
- The meeting module is **not** required: its agenda and minutes badges are
  read when the fields exist, and the app hides the section when the
  capability is absent.

## Licence

LGPL-3. See [`LICENSE`](LICENSE).
