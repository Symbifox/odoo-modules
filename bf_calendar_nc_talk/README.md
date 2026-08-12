# BF Calendar — Nextcloud Talk button (`bf_calendar_nc_talk`)

Adds a **"+ Nextcloud Talk"** button next to "+ Odoo Meeting" on calendar
events.

## Features

- Creates a public Nextcloud Talk conversation through the OCS (Spreed) API.
- Writes the room URL into the event's `videocall_location` field, so that
  invitations and reminders point at the video call.
- Target Nextcloud instance is configured through system parameters.

## Dependencies

`calendar`.

## Licence

Distributed under the **LGPL-3** licence. See the `LICENSE` file.
