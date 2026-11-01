# Calendar Ergonomics

Small comforts for every calendar view of Odoo (meetings, tasks, bookings,
time off...): a refresh button, reload on return to the tab, keyboard
shortcuts, a remembered side panel, *Duplicate* in the event popover and
working hours shaded in the grid.

## Why

Odoo's calendar only reloads when the date, the scale or a filter changes.
Events that change meanwhile (calendar sync every few minutes, an assistant,
a colleague) stay invisible until the whole page is reloaded.

## How it works

- **⟳ Refresh**, next to *Today*: reloads the events, nothing else (date,
  scale, filters and side panel stay as they are).
- **Reload on return**: the calendar reloads when its tab or window comes back
  to the foreground (at most once every 3 seconds).
- **Keyboard shortcuts**, also from the search bar:
  - `Alt+PageUp` / `Alt+PageDown`: previous / next period;
  - `Alt+↑` / `Alt+↓`: wider / narrower scale (day, week, month, year);
  - `Alt+T`: today; `Alt+R`: refresh.

  `Alt+←` / `Alt+→` (browser back and forward) and `Alt+digit` (tab switch on
  Linux) are left to the browser.

  On macOS, Odoo reads `Control` where other systems use `Alt`:
  `Control+PageUp` / `Control+PageDown` (`fn+Control+↑` / `fn+Control+↓` on a
  keyboard without those keys), `Control+T`, `Control+R`. `Control+↑` and
  `Control+↓` belong to Mission Control and App Exposé unless they are turned
  off in the system's keyboard settings.
- **Side panel remembered**: Odoo keeps it open or closed for the current tab
  only; it is now remembered across tabs, in the browser.
- **Duplicate** in the event popover (for whoever may edit the event and
  create in that model): opens a new form filled with the event's values.
  Nothing is created, and no guest is invited, until it is saved. Whoever
  duplicates a meeting is among the copy's guests.

  Never copied, on any model:
  - the link to the source record, one-to-many lines (chatter, activities,
    attendees, which follow from the guests on save), binary fields, fields
    the user may not read, and links to a meeting, an agenda or minutes,
    which exist once per meeting;
  - the video call: the `videocall_location` and `videocall_channel_id`
    fields, a location that is a video call link (the record's own, or a
    Nextcloud Talk `/call/…`, Google Meet, Microsoft Teams, Zoom, Jitsi or
    Whereby room: a room name next to the link is kept), and those links in
    the description (the link and the text that carries it are removed, the
    rest is kept). A meeting link is public: the copy never reuses the
    original's, add one to the copy if needed. Links to other video
    services, or behind a link shortener, are not recognised;
  - what identifies the source record: `access_token`, `event_identifier`
    (the UID of an imported invitation), `nc_task_subfolder` (a task's
    Nextcloud folder);
  - on bookings, the organiser (`user_id`).

  On meetings, only a reviewed list of fields is copied (title, dates,
  description, location, guests, tags, privacy, availability, reminders,
  colour, room...): not the organiser (whoever duplicates organises the copy,
  with their own calendar and as agenda and minutes owner), not the
  opportunity, the status or the recurrence (duplicating an occurrence gives
  one event). Not offered on activities and time off, which are born from
  their record and their approval flow.

  *Duplicate* reads the values as `copy()` would (`copy_data()`) but creates
  through the form: what a module does in its own `copy()` override (for
  example a context set there for linked records) is not played.

  The copied values are form defaults: a *New* made from that same form
  starts from them again, as with any Odoo action opened with defaults.
  Coming back to the calendar gives a blank form again.
- **Working hours shaded** (day and week scales): the time outside the
  current user's working schedule is greyed. The schedule is the employee's
  (else the user's first resource, else the company's), read in its own time
  zone like the rest of Odoo, converted to the browser's; lunch breaks,
  time off and public holidays included. Public holidays are shaded only once
  they are entered in Odoo (*Time Off > Configuration > Public Holidays*, or
  the time off of the working schedule): nothing fills them in by itself.
  When Odoo's `hr_calendar` already shades the meetings calendar (working
  hours of the selected attendees), it is left in charge there.

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 18.0.1.0.0 | 2026-10-31 | Refresh button, reload on return, keyboard shortcuts, remembered side panel, Duplicate (reviewed fields on meetings, never the video call, even in the location or the description, nor the organiser or fields the user may not read; the user is invited to the copy), shaded working hours |

## License

LGPL-3.0
