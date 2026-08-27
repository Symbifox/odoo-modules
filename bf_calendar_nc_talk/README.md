# BF Calendar — Nextcloud Talk button (`bf_calendar_nc_talk`)

Adds a **"+ Nextcloud Talk"** button next to "+ Odoo Meeting" on calendar
events.

## Features

- Creates a public Nextcloud Talk conversation through the OCS (Spreed) API.
- Writes the room URL into the event's `videocall_location` field, so that
  invitations and reminders point at the video call.
- Target Nextcloud instance is configured through system parameters.

- **The button works before the event is saved.** It used to be a plain
  `type="object"` button, so it needed a record on disk and was hidden with
  `not id` — which is why it only ever appeared once you opened the full event
  form, never in the calendar's quick-create popover. It is now intercepted the
  way core intercepts its own "+ Odoo meeting": the server is asked for a room
  and the URL is written into the in-memory record.

## Dependencies

`calendar`.

## Licence

Distributed under the **LGPL-3** licence. See the `LICENSE` file.
