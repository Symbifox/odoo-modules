# Calendar Nextcloud Sync

Bidirectional calendar synchronization between Odoo 18 and both Nextcloud (CalDAV + n8n webhooks) and Google Calendar (OAuth2, API v3).

## Features

- **Bidirectional sync**: Events flow Nextcloud &rarr; Odoo and Odoo &rarr; Nextcloud
- **CalDAV pull sync**: "Pull from Nextcloud" button performs a CalDAV REPORT to fetch all events directly from Nextcloud
- **Incremental sync**: RFC 6578 sync-token support — after the first full pull, the cron only fetches changes (creates/updates/deletes) since the last sync
- **Push pending events**: "Push to Nextcloud" button sends locally-created events to Nextcloud via n8n webhook
- **Webhook push sync**: Real-time event propagation via n8n webhooks on create/update/delete
- **Catch-up cron**: Configurable scheduled sync (default 15 min) as safety net for missed webhooks
- **Full resync cron**: Periodic consistency safety net (every 6 hours) clears sync tokens and re-pulls all events
- **Multi-calendar support**: Configure multiple Nextcloud calendars with independent sync settings
- **Calendar coloring**: Each Nextcloud calendar can be assigned an Odoo color (0-11 palette) applied to its events
- **ETag change detection**: Skips unchanged events during pull sync to avoid unnecessary writes
- **Anti-loop protection**: `x_sync_source` field prevents infinite sync loops
- **Orphan detection**: Events deleted in Nextcloud are automatically removed from Odoo during pull sync
- **Encrypted credentials**: App passwords and webhook secrets stored with Fernet symmetric encryption
- **Instance-level defaults**: Webhook URL and secret configured once in Settings, auto-applied to new calendars
- **Attendee mapping**: Maps ICS `ATTENDEE` mailto: addresses to Odoo partners
- **Recurring events (RRULE)**: NC → Odoo: parses RRULE/EXDATE from ICS, creates Odoo `calendar.recurrence` with occurrence instances
- **VALARM filtering**: Skips nested VALARM/VTIMEZONE blocks in ICS to prevent property collisions
- **Calendar owner**: Explicit `calendar_owner_id` on config ensures synced events appear in the correct user's calendar
- **Windows timezone normalization**: Maps Windows-style TZ names (e.g., "Eastern Standard Time") to IANA
- **RFC 5545 ICS parsing**: Line folding, timezone handling (TZID + pytz), all-day events (exclusive DTEND → inclusive stop_date), DURATION fallback
- **Savepoint isolation**: Each event sync is wrapped in a DB savepoint so failures (e.g., `resource_booking` validation) don't crash the cron
- **Per-config UID scoping**: Event lookups in `create_from_nextcloud` and `delete_from_nextcloud` are scoped to the calendar config, preventing ping-pong when events appear in multiple Nextcloud calendars

## Architecture

```
                         Nextcloud                               Odoo 18
                    (CalDAV Server)                          (Odoo instance)
                 +-----------------------+               +-----------------------+
                 |                       |               |                       |
  CalDAV REPORT  |  /remote.php/dav/     |<-- pull ------| Pull from Nextcloud (manual)     |
  (full pull)    |  calendars/user/cal/  |               | action_pull_from_nextcloud()     |
                 |                       |               |                       |
  sync-collection|                       |<-- incr. -----| Cron (every 15 min)               |
  (incremental)  |                       |               | _pull_incremental() + sync-token |
                 |                       |               |                       |
                 |  webhook_listeners    |--- push ----->|                       |
                 |  CalendarObject*Event |               | calendar.event        |
                 |                       |               | create_from_nextcloud |
                 |                       |<-- push ------| _trigger_sync_webhook |
                 |  CalDAV PUT           |               | (via n8n)             |
                 +-----------------------+               +-----------------------+
                              ^                                    |
                              |          +-------------------+     |
                              +----------| n8n.example.com  |<----+
                                         | (2 workflows)     |
                                         +-------------------+
```

### Sync Paths

| Direction | Mechanism | Trigger |
|-----------|-----------|---------|
| NC &rarr; Odoo (full pull) | CalDAV REPORT (calendar-query) + ICS parsing | Manual "Pull from Nextcloud" button, or cron when no sync-token |
| NC &rarr; Odoo (incremental) | sync-collection REPORT with sync-token (RFC 6578) | Cron every 15 min (when sync-token available) |
| NC &rarr; Odoo (push) | Nextcloud webhook &rarr; n8n &rarr; Odoo JSON-RPC | Automatic on NC event change |
| Odoo &rarr; NC (push) | Odoo model override &rarr; n8n webhook &rarr; CalDAV PUT | Automatic on Odoo event change |
| Odoo &rarr; NC (manual) | "Push to Nextcloud" button &rarr; n8n webhook | Manual, for events pending push |

## File Structure

```
calendar_nextcloud_sync/
+-- __init__.py
+-- __manifest__.py
+-- README.md
+-- models/
|   +-- __init__.py
|   +-- nextcloud_sync_config.py    # Config model, CalDAV operations, ICS parser
|   +-- calendar_event.py           # calendar.event sync extensions
|   +-- res_config_settings.py      # Calendar Settings integration
+-- views/
|   +-- menu.xml                    # Settings > Technical menu
|   +-- calendar_event_views.xml    # Inherited form/list/search + color calendar view
|   +-- nextcloud_sync_config_views.xml  # Config form/list/search + actions
|   +-- res_config_settings_views.xml    # Calendar Settings page
+-- data/
|   +-- ir_actions_server.xml       # Automated actions for sync logging
|   +-- nextcloud_sync_cron.xml     # Catch-up cron (15 min) + full resync cron (6h)
+-- security/
|   +-- ir.model.access.csv         # ACL: read for users, full for admins
+-- migrations/
    +-- 18.0.1.1.0/
        +-- pre-migrate.py          # Column rename for encryption migration
```

## Installation

```bash
# Upgrade module
docker exec odoo odoo -d mydb -u calendar_nextcloud_sync \
    --stop-after-init --http-port=9665

# Restart
docker restart odoo
```

## Configuration

### Instance-Level Settings (Settings > Calendar)

Under **Settings > Calendar > Nextcloud Calendar Sync**:

| Setting | Description |
|---------|-------------|
| n8n Webhook URL | Default URL applied to new calendar configs |
| Webhook Secret | Default secret applied to new calendar configs (password field) |
| Enable Catch-up Sync | Toggle the cron job on/off |
| Sync Interval (minutes) | Cron frequency (default: 15 min) |
| Configure Nextcloud Calendars | Link to the calendar config list |

Set the webhook URL and secret here once — they will auto-populate when creating new calendar configurations.

### Per-Calendar Configuration

Go to **Settings > Technical > Nextcloud Calendar Sync** (or use the link in Calendar Settings):

| Field | Description | Example |
|-------|-------------|---------|
| Calendar Name | Display name | `Personal` |
| Nextcloud URL | Base URL | `https://nextcloud.example.com` |
| CalDAV Path | Calendar path | `/remote.php/dav/calendars/username/personal/` |
| Nextcloud User | Calendar owner | `username` |
| App Password | Nextcloud app password (encrypted at rest) | *(from Nextcloud > Settings > Security)* |
| Odoo Color | Color picker (0-11 palette) for events in calendar views | *(click to pick)* |
| Sync Direction | `Bidirectional`, `NC -> Odoo`, or `Odoo -> NC` | `Bidirectional` |
| Calendar Owner | Odoo user who will see synced events in their calendar | `Jane Doe` |
| Webhook URL | n8n webhook for Odoo &rarr; NC *(auto-filled from Settings)* | `https://n8n.example.com/webhook/odoo-to-nc` |
| Webhook Secret | Shared auth token *(auto-filled from Settings)* | *(auto-encrypted)* |

Then:
1. Click **Test Connection** to verify CalDAV access (PROPFIND)
2. Click **Pull from Nextcloud** to pull all events from Nextcloud

### n8n Environment Variables

| Variable | Description |
|----------|-------------|
| `NC_WEBHOOK_SECRET` | Secret for NC &rarr; Odoo webhook authentication |
| `ODOO_WEBHOOK_SECRET` | Secret for Odoo &rarr; NC webhook authentication |
| `ODOO_DB` | Odoo database name (`mydb`) |
| `ODOO_USER_ID` | Odoo API user ID |
| `ODOO_API_KEY` | Odoo API key |

### n8n Credentials

1. **Odoo API** (HTTP Header Auth): `Authorization: Bearer <api_key>`
2. **Nextcloud CalDAV** (HTTP Basic Auth): username + app password

### Nextcloud Webhook Registration

Register webhooks via OCS API for real-time push sync:

```bash
curl -X POST "https://nextcloud.example.com/ocs/v2.php/apps/webhook_listeners/api/v1/webhooks" \
  -H "OCS-APIRequest: true" \
  -H "Content-Type: application/json" \
  -u "admin:app-password" \
  -d '{
    "httpMethod": "POST",
    "uri": "https://n8n.example.com/webhook/nc-to-odoo",
    "event": "OCA\\DAV\\Events\\CalendarObjectCreatedEvent",
    "headers": {"X-Webhook-Secret": "your-secret-here"}
  }'

# Repeat for CalendarObjectUpdatedEvent and CalendarObjectDeletedEvent
```

## Models

### nextcloud.calendar.sync.config

Configuration for each Nextcloud calendar connection.

**Key Methods:**

| Method | Description |
|--------|-------------|
| `action_pull_from_nextcloud()` | Full CalDAV REPORT pull of all VEVENTs with ETag skip + orphan deletion; stores sync-token after success |
| `action_push_to_nextcloud()` | Push pending Odoo events to Nextcloud via n8n webhook |
| `action_force_full_sync()` | Clear sync-token and perform a full pull (manual "Force Full Resync" button) |
| `action_test_connection()` | PROPFIND to verify CalDAV endpoint is reachable |
| `action_view_events()` | Opens calendar events (colored by `odoo_color`) |
| `update_sync_status(status, msg)` | Updates last_sync fields (called by webhook handler) |
| `_cron_sync_all()` | Cron entry point (15 min): tries incremental sync if token available, falls back to full pull |
| `_cron_full_resync()` | Full resync cron (6h): clears sync tokens and re-pulls all events as consistency safety net |
| `_pull_incremental()` | RFC 6578 sync-collection REPORT with stored token; processes only changes since last sync |
| `_store_sync_token()` | PROPFIND Depth:0 to fetch and store the current sync-token |
| `_parse_sync_collection_response()` | Parse sync-collection 207 response into changed events + deleted hrefs + new token |

**Full Pull (`action_pull_from_nextcloud`) Flow:**

1. Send CalDAV REPORT (calendar-query) with `Depth: 1` to fetch all VEVENTs
2. Parse 207 Multi-Status XML: extract `<d:href>`, `<d:getetag>`, `<c:calendar-data>`
3. Parse each ICS blob: unfold lines (RFC 5545 &sect;3.1), extract UID/SUMMARY/DTSTART/DTEND/LOCATION/DESCRIPTION/ATTENDEE/RRULE/EXDATE (skips nested VALARM/VTIMEZONE)
4. **ETag skip**: if event exists in Odoo with same ETag, skip it (unchanged)
5. **Upsert**: call `calendar.event.create_from_nextcloud()` for new/changed events
6. **Orphan detection**: delete Odoo events whose UID was not in the REPORT response
7. **Store sync-token**: PROPFIND to fetch and save the token for incremental syncs
8. Return notification with counts: created / updated / deleted / unchanged / errors

**Incremental Sync (`_pull_incremental`) Flow:**

1. Send `sync-collection` REPORT with stored `sync-token` and `sync-level: 1`
2. If **412 Precondition Failed** (token expired): clear token, fall back to full pull
3. Parse 207 response: `<d:status>` with 200 = changed (has ICS data), 404 = deleted (href only)
4. Extract new `<d:sync-token>` from response root
5. **Upsert** changed events via `create_from_nextcloud()`
6. **Delete** removed events by looking up `x_caldav_href` and calling `delete_from_nextcloud()`
7. Store new sync-token for next run

**Cron (`_cron_sync_all`):**

Runs every 15 minutes (configurable in Settings). Searches for configs where:
- `active = True`
- `sync_direction` is `both` or `nc_to_odoo`
- `nextcloud_app_password_encrypted` is set

For each config:
- If `caldav_sync_token` is set &rarr; `_pull_incremental()` (lightweight, only delta)
- Otherwise &rarr; `action_pull_from_nextcloud()` (full pull, stores token for next time)

Per-config error handling so one failure doesn't block others. Each event sync is wrapped in a `cr.savepoint()` with `flush_all()` so deferred validation errors (e.g., `resource_booking._compute_state`) are caught per-event instead of crashing the entire cron.

A separate **full resync cron** runs every 6 hours, clearing sync tokens and performing a full CalDAV pull for all configs. This acts as a consistency safety net against drift.

**ICS Date Parsing:**

| Format | Example | Interpretation |
|--------|---------|---------------|
| `VALUE=DATE` | `20260214` | All-day event |
| UTC (Z suffix) | `20260214T100000Z` | UTC datetime |
| `TZID=...` | `TZID=America/Montreal:20260214T100000` | Localized, converted to UTC via pytz |
| `DURATION` | `PT1H30M` | Fallback if no DTEND |

### calendar.event (Inherited)

**Added Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `x_caldav_etag` | Char | ETag from Nextcloud for change detection |
| `x_sync_source` | Selection | `odoo` or `nextcloud` (origin marker) |
| `x_caldav_href` | Char | Full CalDAV URL of event in Nextcloud |
| `x_nc_calendar_id` | Many2one | Link to sync configuration |
| `x_last_sync` | Datetime | Last synchronization timestamp |
| `x_nc_uid` | Char | ICS UID (unique identifier in Nextcloud) |
| `color` | Integer | Color index (0-11), set from config's `odoo_color` during sync |

**API Methods (called via JSON-RPC):**

| Method | Args | Description |
|--------|------|-------------|
| `create_from_nextcloud(data, config_id)` | `{uid, summary, start, end, allday, location, description, etag, href, attendees, rrule, exdates, event_tz}` | Upsert event from Nextcloud (handles both single and recurring events) |
| `delete_from_nextcloud(nc_uid, config_id)` | NC UID string | Delete event removed from NC (handles recurrence + all instances) |
| `update_etag_from_nextcloud(event_id, etag, href)` | Odoo ID + new ETag | Update ETag after successful NC PUT |

**Sync Context Flags** (suppresses side-effects during sync):

```python
{
    "no_mail_to_attendees": True,   # No Odoo email invitations
    "skip_nc_sync": True,           # Don't trigger outbound webhook
    "mail_create_nolog": True,      # No mail log entries
    "tracking_disable": True,       # No field tracking messages
    "dont_notify": True,            # Suppress all notifications
}
```

**Model Overrides:**

- `create()`: Sets `x_sync_source='odoo'`, generates UUID, triggers Odoo &rarr; NC webhook
- `write()`: Triggers webhook on sync-relevant field changes (name, start, stop, etc.)
- `unlink()`: Triggers webhook before deletion to capture event data

### res.config.settings (Inherited)

Adds fields to **Settings > Calendar**:

| Field | Storage | Description |
|-------|---------|-------------|
| `nc_sync_webhook_url` | `ir.config_parameter` | Default webhook URL for new configs |
| `nc_sync_webhook_secret` | `ir.config_parameter` | Default webhook secret for new configs |
| `nc_sync_cron_enabled` | Cron `active` field | Toggle catch-up cron on/off |
| `nc_sync_cron_interval` | Cron `interval_number` field | Minutes between catch-up syncs (default: 1) |

## Calendar Coloring

Odoo's calendar view uses an integer palette (0-11), not hex colors. Each sync config has an `odoo_color` field with a color picker widget. When events are synced from Nextcloud, they inherit the config's color.

The "View Events" button on a config opens a dedicated calendar view that groups events by this `color` field, so events from different Nextcloud calendars appear in distinct colors.

The main Odoo Calendar app is not affected — it continues using its standard partner-based coloring.

## Security

| Model | Group | Read | Write | Create | Delete |
|-------|-------|------|-------|--------|--------|
| `nextcloud.calendar.sync.config` | Internal User | Yes | No | No | No |
| `nextcloud.calendar.sync.config` | System (Admin) | Yes | Yes | Yes | Yes |

- App passwords encrypted at rest with Fernet (key in `ir.config_parameter`)
- Webhook secrets encrypted with the same mechanism
- Instance-level webhook settings stored in `ir.config_parameter` (admin-only access)
- Nextcloud sync fields on calendar.event visible only with Developer Mode (`base.group_no_one`)
- All communications over HTTPS

## Anti-Loop Protection

```
Odoo create/write/unlink
    |
    +-- x_sync_source == 'nextcloud'?  --> SKIP (came from NC)
    +-- context has skip_nc_sync?      --> SKIP (internal operation)
    +-- sync_direction == 'nc_to_odoo'? --> SKIP (config says no outbound)
    |
    +-- Otherwise: trigger n8n webhook --> NC CalDAV PUT
```

The same logic applies in reverse: events arriving from Nextcloud are created with `x_sync_source='nextcloud'` and `skip_nc_sync=True`, preventing them from bouncing back.

## Automated Actions

Three `base.automation` records (in `data/ir_actions_server.xml`) log sync events:

| Action | Trigger | Filter |
|--------|---------|--------|
| Calendar Event Created | `on_create` | `x_nc_calendar_id != False AND x_sync_source != 'nextcloud' AND recurrency = False` |
| Calendar Event Updated | `on_write` | Same + monitored fields: name, start, stop, allday, location, description, privacy, show_as |
| Calendar Event Deleted | `on_unlink` | Same (runs before deletion) |

## Recurring Events (RRULE)

Since v18.0.1.16.0, **Nextcloud → Odoo** sync fully supports recurring events:

- **RRULE parsing**: `FREQ=WEEKLY;BYDAY=MO,WE;INTERVAL=2`, `FREQ=YEARLY`, etc.
- **Odoo recurrence pipeline**: Sets `recurrency=True` + `rrule` on creation, which triggers Odoo's `calendar.recurrence` to generate up to 720 occurrence instances
- **EXDATE support**: Deleted occurrences (ICS `EXDATE`) are removed from Odoo after instance generation
- **Timezone handling**: `TZID` from `DTSTART` is mapped to `event_tz` for correct DST transitions; Windows timezone names (e.g., "Eastern Standard Time") are normalized to IANA
- **Instance post-processing**: Generated instances inherit `x_nc_calendar_id`, `x_sync_source`, `color`, and `partner_ids` from the base event (since `copy=False` fields are not auto-copied by `_apply_recurrence`)
- **Delete + recreate strategy**: When a recurring event changes in Nextcloud (etag differs), the entire recurrence and all instances are deleted and recreated from scratch
- **VALARM filtering**: The ICS parser skips nested `BEGIN:VALARM` blocks to prevent alarm properties (e.g., `SUMMARY:Alarm notification`) from overwriting the actual event summary
- **Outbound sync remains blocked**: Odoo → Nextcloud sync of recurring events is not supported (the `recurrency=False` filter on create/write/unlink overrides is preserved)

### Calendar Owner

Each sync config has a `calendar_owner_id` field (Many2one to `res.users`). During sync, this user's partner is added to `partner_ids` on all events, ensuring they appear in the user's Odoo calendar view. This is critical because the cron runs as OdooBot — without explicit owner resolution, events would only be visible to OdooBot.

Falls back to matching `nextcloud_user` against Odoo login if `calendar_owner_id` is not set, then to `self.env.user` as last resort.

## Limitations

1. **Recurring events (outbound)**: Odoo → Nextcloud sync of recurring events is not supported
2. **RECURRENCE-ID overrides**: Modified single instances in Nextcloud (ICS `RECURRENCE-ID`) are not synced — only the base VEVENT with RRULE is used
3. **Attendee creation**: If an attendee email doesn't exist in `res.partner`, it's skipped (not auto-created)
4. **Nextcloud webhook latency**: Default 5 minutes (requires dedicated Nextcloud worker to reduce)
5. **Single Odoo calendar**: Odoo has no concept of named calendars; `x_nc_calendar_id` is the closest grouping
6. **Color palette**: Odoo supports only 12 colors (0-11), not arbitrary hex codes

## Troubleshooting

| Issue | Check |
|-------|-------|
| "Pull from Nextcloud" not visible | App password must be configured (encrypted field not empty) |
| Sync direction error | Config must be `Bidirectional` or `NC -> Odoo` for pull sync |
| Connection test fails | URL reachable? CalDAV path correct? App password (not main password)? |
| Events duplicated | Check `x_nc_uid` uniqueness; ensure no duplicate configs for same calendar |
| Attendees not mapped | Verify emails exist in `res.partner`; check case sensitivity |
| Sync shows 0 events | Verify CalDAV path points to a calendar (not the user root) |
| Encryption errors | `cryptography` library installed? Check `ir.config_parameter` for encryption key |
| Push sync not working | n8n running? Webhook secrets match? Check n8n execution logs |
| Cron not running | Check Settings > Calendar > catch-up toggle is enabled |
| Webhook defaults not applied | Set URL/secret in Settings > Calendar before creating new configs |
| Events all same color | Set `Odoo Color` on each sync config via the color picker |

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 18.0.2.7.0 | 2026-07-06 | Latest 2.x line: Google Calendar backend (OAuth2, API v3) alongside Nextcloud, per-config backend selection, self-alias attendee stripping, organizer-based calendar routing (new events route to the sync calendar owned by the organizer, falling back to the instance default), shared timezone normalization via `bf_timezone` |
| 18.0.1.23.0 | 2026-03-16 | Fix cron crash (savepoint isolation for resource_booking ValidationError), fix all-day events spanning 2 days (RFC 5545 exclusive DTEND), fix cron vs button inconsistency (scope UID search per calendar config), add full resync cron (6h safety net), fix cron intervals (15 min catch-up, 6h full resync) |
| 18.0.1.18.0 | 2026-02-15 | Fix VALARM property collision: skip nested ICS components (VALARM, VTIMEZONE) during parsing |
| 18.0.1.17.0 | 2026-02-15 | Add `calendar_owner_id` field for explicit calendar owner resolution (fixes invisible events for shared calendars) |
| 18.0.1.16.0 | 2026-02-15 | RRULE recurring event support (NC→Odoo): RRULE/EXDATE parsing, `calendar.recurrence` creation, instance post-processing, Windows TZ mapping |
| 18.0.1.15.0 | 2026-02-14 | Incremental sync via RFC 6578 sync-token; cron reduced to 1 min; "Force Full Resync" button |
| 18.0.1.8.0 | 2026-02-14 | "Push to Nextcloud" button; renamed buttons (Pull/Push); respects sync direction for button visibility |
| 18.0.1.7.0 | 2026-02-14 | Fix: calendar assignment triggers outbound sync + UUID generation; color auto-repair during pull sync |
| 18.0.1.6.0 | 2026-02-14 | Instance-level webhook defaults in Calendar Settings (auto-fill new configs) |
| 18.0.1.5.0 | 2026-02-14 | Calendar Settings integration (cron toggle, interval, config link) |
| 18.0.1.4.0 | 2026-02-14 | Catch-up cron job (15 min default), `_cron_sync_all()` method |
| 18.0.1.3.0 | 2026-02-14 | Calendar coloring: `odoo_color` on config, `color` on events, dedicated calendar view |
| 18.0.1.2.0 | 2026-02-14 | CalDAV REPORT pull sync ("Pull from Nextcloud" button), ICS parser, ETag skip, orphan detection |
| 18.0.1.1.0 | 2026-02-13 | Fernet encryption for app passwords and webhook secrets |
| 18.0.1.0.0 | 2026-02-13 | Initial release: bidirectional sync via n8n webhooks |

## License

LGPL-3.0
