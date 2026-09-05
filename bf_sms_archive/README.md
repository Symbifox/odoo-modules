# SMS & Calls for Odoo 18

A two-way SMS/MMS messaging and archiving module for Odoo 18. Send and receive live SMS/MMS through VOIP.ms from a built-in chat workspace, and import Android SMS/MMS backups and call history from [SMS Backup & Restore](https://www.synctech.com.au/sms-backup-restore/). Search conversations, browse call logs, link records to project tasks, export branded PDF reports, and manage confidential threads.

## Features

### Import & Deduplication
- **XML/ZIP import** -- Upload SMS Backup & Restore exports directly via wizard
- **Auto-detection** -- Automatically detects SMS exports (`<smses>`) vs call log exports (`<calls>`) and dispatches to the right parser
- **SMS + MMS support** -- Parses both `<sms>` and `<mms>` tags, extracts text and attachments from MMS parts
- **Call log support** -- Parses `<call>` tags with type (incoming, outgoing, missed, voicemail, rejected, blocked), duration, and presentation
- **Streaming parser** -- Uses `iterparse` for memory-efficient processing of large files (200 MB+)
- **SHA-256 deduplication** -- Hash of `phone|timestamp|body` (SMS) or `phone|timestamp|duration|type` (calls) prevents duplicate imports
- **Batch processing** -- Creates records in batches of 500 for performance
- **Auto-match contacts** -- Links threads to existing Odoo contacts by phone number after import
- **Multi-file ZIP** -- ZIP archives containing both `sms-*.xml` and `calls-*.xml` are processed in a single import

### Conversations & Search
- **Thread grouping** -- Messages and calls grouped by normalized E.164 phone number
- **Unified thread** -- A single thread per contact shows both SMS and calls, with separate tabs
- **Full-text search** -- Search across all message bodies from the Messages list
- **Direction filtering** -- Filter by received, sent, or draft messages
- **Chronological view** -- Messages displayed in conversation order within threads
- **Contact linking** -- Manual or automatic linking to `res.partner` records
- **Number write-back (v18.0.5.10.0+)** -- Linking a thread to a contact writes the thread's number into that contact's **Mobile** field, so the number is reachable from the contact record and not just from the messenger. Never overwrites: a number already on file (in `mobile` **or** `phone`, in any format) is left alone, and a *different* mobile on file is kept, with a chatter note on the contact recording the SMS number instead. Creating a contact from an unknown number fills `mobile` (not `phone`) for the same reason.

### Call Log
- **Call type badges** -- Colour-coded badges: green (incoming), blue (outgoing), salmon (missed), yellow (voicemail), grey (rejected/blocked)
- **Duration display** -- Human-readable format: "45s", "2m 05s", "1h 23m 10s"
- **Integrated view** -- Call history visible in the thread form alongside SMS messages
- **Recording link** (`recording_url`) -- Nextcloud internal link to the audio file for VOIP.ms-sourced calls (set by the external `sync_voipms_recordings.py` sync job). File itself stays on Nextcloud so the Odoo filestore doesn't bloat.

### Dashboard
- **OWL component** -- Custom real-time dashboard built with Odoo Web Library (OWL)
- **KPI cards** -- Clickable cards for total messages, calls, contacts, and unmatched threads
- **Monthly trends** -- Stacked progress bars for SMS (received/sent) and calls (incoming/outgoing/missed) by month
- **Top 10 contacts** -- Interactive table ranked by total interactions (SMS + calls) within the selected period
- **Call statistics** -- Average duration, total talk time, longest call, missed rate with colour-coded thresholds
- **Date range filter** -- Filter all data by period: 3 months, 6 months, 1 year, 2 years, all time, or custom date range
- **Landing page** -- Dashboard is the default home screen when opening the module

### PDF Export
- **Branded PDF reports** -- iMessage-style chat bubbles with company branding
- **Dynamic colours** -- Reads brand colours from `res.company` fields (set via `bf_lexend` or defaults to Odoo purple/dark)
- **Company logo & name** -- Header and footer use the current company's logo and name
- **Emoji support** -- Supplementary plane emoji mapped to BMP equivalents for wkhtmltopdf, with embedded NotoEmoji font
- **HEIC/WEBP images** -- MMS images auto-converted to JPEG for PDF rendering via Pillow

### CSV & XML Export
- **CSV export** -- Download conversation as CSV (Date, Direction, Contact, Body, MMS)
- **XML export** -- Re-export in SMS Backup & Restore format for portability

### Shared lines (5.9+)
- **Per-user numbers** -- Every line (DID) has an owner; conversations held on it are that owner's data by default
- **Line sharing** -- Add colleagues to a line's `user_ids` and they can send from that number and read the messages held on it, without gaining access to the owner's other lines
- **Scoped to the line, not the thread** -- A thread is keyed by (phone, owner) and can mix lines: an imported history and a business exchange on a shared number may share one thread. A shared colleague reads only what actually travelled on the shared number; the messenger says how many messages are out of reach rather than pretending the conversation is complete
- **Read-only for guests** -- A shared colleague uses the line but cannot rename it, retarget its owner or regenerate its webhook token, and cannot take ownership of a thread, move it to another line or flip its confidentiality
- **Call log never shared** -- Calls carry no line (they come from the Android journal), so sharing a number gives no access to the owner's call history
- **Dashboard stays personal** -- Statistics are the owner's; a shared reader gets no volumes on messages they cannot read
- **Notifications follow the share** -- Bus, Web Push and mobile push reach every user of the line, not just the owner

### Message origin (5.9+)
- **Per-message line badge** -- As soon as the instance has more than one number, each bubble names the line the message travelled on, in small grey type next to the timestamp
- **Sender name on shared lines** -- An outgoing message written by a colleague shows who wrote it
- **Reply-line guard** -- When the selected send number differs from the one the last exchange used, the composer says so and offers a one-click switch

### Confidentiality
- **Hidden threads** -- Mark any conversation as "Confidentiel" to exclude it from MCP searches, default list views and line sharing
- **Owner-first security** -- Record rules scope data to its owner, widened only by an explicit line share
- **Manager override** -- `group_sms_manager` can access all data for administration

### Posting onto a record
- **Post to any record (5.8+)** -- Select messages or calls and post them as one consolidated chatter note. The destination is picked through [`bf_chatter_target`](../bf_chatter_target): a single search box over every chatter-bearing model, with no project and no object type to choose first. Before 5.8 the wizard asked for a project, then a task, and could reach nothing else.
- **Thread <> Task linking** -- Many2many relationship between conversations and project tasks. Linking the conversation and relaying its later messages are task-only by construction, so those two options appear only when the chosen destination *is* a task.
- **Auto-follow** -- A conversation can be pinned to a task so subsequent messages relay to its chatter on their own.
- **MCP tools** -- 9 tools for searching, browsing, and linking SMS and call data programmatically

## Installation

### Dependencies

- Odoo 18 Community or Enterprise
- Python: `defusedxml` (required for safe XML parsing)
- Python: `pillow-heif` (optional, for HEIC image conversion in MMS)
- Odoo modules: `base`, `mail`, `project`, `bf_onboarding_base`, `bf_chatter_target`
- Optional: `bf_lexend` -- Provides Lexend font and brand colour settings. Without it, reports use system-ui font and default Odoo colours.

### Install via CLI

```bash
# Stop Odoo first
docker stop my-odoo

# Install the module
docker compose run --rm my-odoo odoo -d my-database \
    -i bf_sms_archive --stop-after-init --no-http

# Restart
docker start my-odoo
```

### Post-Install

1. Go to **Settings > Users** and assign the **Archive SMS & Appels > Utilisateur SMS** or **Gestionnaire SMS** group
2. The **Archive SMS & Appels** app appears in the main menu
3. (Optional) Install `bf_lexend` for Lexend font and custom brand colours in PDF reports

## Usage

### Importing SMS

1. Export from **SMS Backup & Restore** on Android (XML or ZIP format)
2. Open **Archive SMS & Appels > Importer**
3. Upload the file and click **Importer**
4. Results show: messages processed, created, duplicates skipped, threads, contacts matched

Re-importing the same file is safe -- duplicates are detected by hash and skipped.

### Importing Call Logs

1. Export call history from **SMS Backup & Restore** on Android (`calls-*.xml`)
2. Open **Archive SMS & Appels > Importer**
3. Upload the file -- the wizard auto-detects the call log format
4. Results show: calls processed, created, duplicates skipped, threads, contacts matched

ZIP files containing both SMS and call log XML files are imported in a single pass.

### Managing Conversations

- **Archive SMS & Appels > Conversations** -- Browse all threads, sorted by last message date
- Click a thread to see all messages and calls in separate tabs
- Use the **Lier au contact Odoo** button to match a thread to a contact
- Toggle **Confidentiel** to hide a thread from MCP searches

### Browsing Call Logs

- **Archive SMS & Appels > Journal d'appels** -- Browse all calls with type badges and duration
- Filter by type (incoming, outgoing, missed, voicemail), date range, or contact
- Graph and pivot views show call volume trends by month and type

### Exporting

- **PDF** -- Click **Imprimer** on a thread for a branded PDF with chat bubbles
- **CSV** -- Click **Exporter CSV** for a spreadsheet-friendly format
- **XML** -- Click **Exporter XML** for SMS Backup & Restore compatible format

### Posting onto a record

**From a thread:**
- Open the **Taches liees** tab and add project tasks

**From messages or calls:**
- Select them in the list and use **Action -> Poster sur une fiche**. Search the destination by name, by number, by a shorthand (`task:22299`) or by pasting an Odoo URL; the wizard previews the note before it is posted.
- Linking the conversation and relaying later messages are offered only when the destination is a task.

### MCP Tools

When `HAS_SMS_ARCHIVE=true` is set in the org `.env`, 9 tools are available:

| Tool | Description |
|------|-------------|
| `sms_list_threads` | List conversation threads (excludes hidden by default) |
| `sms_get_thread` | View full conversation with optional date range filter |
| `sms_get_thread_by_phone` | Find a thread by phone number |
| `sms_search_messages` | Full-text search with phone, direction, date filters |
| `sms_import_backup` | Import XML/ZIP from server filesystem path (auto-detects SMS vs calls) |
| `sms_link_thread_to_task` | Link a thread to a project task |
| `call_list_calls` | List call log entries with phone, type, and date filters |
| `call_get_calls_for_thread` | View call history for a conversation thread |
| `call_search_calls` | Search calls by contact, type, minimum duration, date range |

## Data Model

### Core Models

| Model | Description |
|-------|-------------|
| `sms.archive.thread` | Conversation thread (one per phone number per owner) -- shared by SMS and calls |
| `sms.archive.message` | Individual SMS/MMS message |
| `sms.archive.mms.part` | MMS attachment (image, video, audio) linked to a message |
| `call.archive.call` | Individual call log entry |
| `sms.archive.dashboard` | Dashboard data aggregation (no table, SQL queries) |
| `sms.archive.import.wizard` | TransientModel for file upload and import |

### sms.archive.thread

| Field | Type | Description |
|-------|------|-------------|
| `phone_normalized` | Char (indexed, unique with owner) | E.164 normalized phone number |
| `phone_raw` | Char | Original number from XML |
| `contact_name` | Char | Last known name from backup |
| `partner_id` | Many2one (`res.partner`) | Linked Odoo contact |
| `owner_id` | Many2one (`res.users`) | Data owner (current user) |
| `line_id` | Many2one (`sms.archive.line`, indexed) | Line the conversation opened on -- drives sharing |
| `is_hidden` | Boolean | Exclude from MCP searches |
| `task_ids` | Many2many (`project.task`) | Linked project tasks |
| `message_count` | Integer (computed, stored) | Number of messages |
| `last_message_date` | Datetime (computed, stored) | Most recent message timestamp |
| `last_message_preview` | Char (computed, stored) | First 100 chars of last message |
| `call_count` | Integer (computed, stored) | Number of calls |
| `last_call_date` | Datetime (computed, stored) | Most recent call timestamp |

### sms.archive.message

| Field | Type | Description |
|-------|------|-------------|
| `thread_id` | Many2one (`sms.archive.thread`) | Parent conversation |
| `message_hash` | Char(64) (indexed, unique) | SHA-256 dedup key |
| `direction` | Selection (`in`/`out`/`draft`) | Message direction |
| `body` | Text | Message content |
| `date_sent` | Datetime (indexed) | Send/receive timestamp |
| `date_sent_ms` | Char | Raw millisecond timestamp from XML |
| `is_mms` | Boolean | Whether this was an MMS |
| `contact_name` | Char | Contact name from backup |
| `owner_id` | Many2one (related, stored) | Owner via thread |
| `line_id` | Many2one (`sms.archive.line`) | Line the message travelled on (live messages) |
| `sent_by_id` | Many2one (`res.users`) | Who actually wrote an outgoing message (shared lines) |
| `import_batch_id` | Char | Backup set identifier |
| `mms_part_ids` | One2many (`sms.archive.mms.part`) | MMS attachments |

### call.archive.call

| Field | Type | Description |
|-------|------|-------------|
| `thread_id` | Many2one (`sms.archive.thread`) | Parent conversation |
| `call_hash` | Char(64) (indexed, unique) | SHA-256 dedup key |
| `call_type` | Selection | `incoming`, `outgoing`, `missed`, `voicemail`, `rejected`, `blocked` |
| `date` | Datetime (indexed) | Call timestamp |
| `date_ms` | Char | Raw millisecond timestamp from XML |
| `duration` | Integer | Call duration in seconds |
| `duration_display` | Char (computed, stored) | Human-readable duration |
| `contact_name` | Char | Contact name from backup |
| `owner_id` | Many2one (related, stored) | Owner via thread |
| `import_batch_id` | Char | Backup set identifier |
| `presentation` | Selection | `allowed`, `restricted`, `unknown`, `payphone` |
| `recording_url` | Char | Nextcloud internal link to audio file (VOIP.ms sync) |

### sms.archive.mms.part

| Field | Type | Description |
|-------|------|-------------|
| `message_id` | Many2one (`sms.archive.message`) | Parent message |
| `content_type` | Char | MIME type (image/jpeg, image/heic, etc.) |
| `filename` | Char | Original filename |
| `is_image` | Boolean (computed) | Whether this part is an image |
| `attachment_id` | Many2one (`ir.attachment`) | Stored binary data |

## Branding

The PDF report reads brand colours from `res.company`:

| Field | Default | Description |
|-------|---------|-------------|
| `report_brand_primary` | `#714B67` (Odoo purple) | Accent colour (banner label, sent bubbles, accent bar) |
| `report_brand_dark` | `#212529` (Bootstrap dark) | Dark background (banner) |

These fields are provided by the `bf_lexend` module. If `bf_lexend` is not installed, the defaults above are used. The Lexend font CSS is also conditionally loaded only when `bf_lexend` is present -- otherwise the report falls back to `system-ui, sans-serif`.

## Security

### Groups

| Group | Access |
|-------|--------|
| **Utilisateur SMS** (`group_sms_user`) | Read/write/create own data only. No delete. |
| **Gestionnaire SMS** (`group_sms_manager`) | Full CRUD on all data. Inherits user group. |

### Record Rules

For the user group:

| Model | User rule |
|-------|-----------|
| `sms.archive.thread` | `owner_id = user.id`, or the thread's `line_id` is shared with the reader and the thread is not confidential |
| `sms.archive.message` | `owner_id = user.id`, or **the message's own `line_id`** is shared with the reader and its thread is not confidential |
| `sms.archive.mms.part` | Follows its message's line |
| `call.archive.call` | `owner_id = user.id` only -- calls carry no line |
| `sms.archive.line` | Readable by owner and shared users; writable, creatable and deletable by the owner alone (two rules split by permission) |

Message access is deliberately scoped by the **message's** line rather than the thread's. Scoping by thread would hand a colleague the owner's whole imported history the moment an archived contact texted the shared number. `sms.archive.thread.write()` additionally refuses `owner_id`, `line_id` and `is_hidden` changes from anyone but the owner (or a manager), so a shared reader cannot promote themselves. Manager group has `[(1, '=', 1)]` (unrestricted).

### Privacy Considerations

- All data is scoped per-user via `owner_id`; the only cross-user path is a line share, which is deliberate, visible on the line form and limited to non-confidential threads
- Sharing a line shares the messages held **on that line**, including those that predate the share -- the line form says so before you save. It does not share the rest of the thread, the call log, or the dashboard
- Residual boundary: on a mixed thread, the backend thread list still shows owner-scoped aggregates (`message_count`, `last_message_date`) to a shared reader. The messenger recomputes preview and unread counts per reader; the backend list does not. No message body is exposed there
- Phone numbers are PII -- acceptable risk for self-hosted personal instance
- No SMS or call content is written to Odoo logs (only IDs and counts)
- VOIP.ms API credentials are read from `ir.config_parameter` (never hardcoded) and
  redacted from any error message; the method whitelist blocks anything outside
  read + messaging + callback-config calls
- Data is included in standard Odoo backups (DB + filestore)

### Public HTTP endpoints

Everything the phone talks to is declared `auth="public"` — bar `…/auth/start`,
which is `auth="user"` on purpose — because none of it rides an Odoo session.
That is the routing declaration, not the access model: each request carries its
own secret and is refused without it.

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `POST /bf_sms_archive/api/ingest` | `Authorization: Bearer <device token>` | Live push from the Android companion app (`sms.archive.device`) |
| `GET/POST /bf_sms_archive/api/voipms/sms` | `?token=<per-line webhook token>` | VOIP.ms SMS/MMS URL Callback (inbound messages) |
| `/bf_sms_archive/mobile/v1/*` (the whole mobile API: threads, conversation, send, contacts, push registration…) | `Authorization: Bearer <device token>` | The companion app's read/write surface |

Three routes of that mobile API are the pairing path and therefore carry no
token yet: `GET …/ping` returns liveness and no data; `POST …/login` is the
legacy password path, capped per IP and per login, answering with a **uniform**
failure so it cannot be used as a credential oracle, and refusing outright any
account with TOTP enabled; `POST …/auth/exchange` trades a one-time pairing
code for a device token and requires the PKCE verifier. The recommended path is
`…/auth/start`, which delegates to `/web/login` and therefore inherits SSO and
the second factor.

Three more routes are public metadata the platform must read while signed out:
`/bf_sms_archive/push-sw.js` and `/bf_sms_archive/manifest.webmanifest` (Web
Push service worker and PWA manifest), and `/.well-known/assetlinks.json`
(Android Digital Asset Links, so the app can claim its own HTTPS links).

The webhook callback URL embeds the line's secret token (VOIP.ms offers no signature),
so the URL must be treated as a secret and kept out of access logs. Inbound MMS media
URLs are fetched only when they resolve to **public** hosts (private/loopback/link-local
and cloud-metadata addresses are blocked — anti-SSRF), with no redirects and a streamed
size cap. Outbound calls go only to `https://voip.ms/api/v1/rest.php`.

### Outbound network calls

- VOIP.ms REST API (`sendSMS`/`sendMMS`/`getSMS`/`getDIDsInfo`/`setSMS`) for live messaging
- Inbound MMS media downloads from public hosts only (SSRF-guarded)

## Menu Structure

```
Archive SMS & Appels
|-- Tableau de bord       (OWL dashboard with KPIs, trends, top contacts, date filter)
|-- Conversations         (thread list, "Visibles" filter active by default)
|-- Journal d'appels      (call log list with type badges and duration)
|-- Rechercher            (all messages, full-text search)
+-- Importer              (upload XML/ZIP wizard, auto-detects SMS vs calls)
```

## Phone Normalization

The import wizard normalizes phone numbers for consistent thread grouping:

| Input | Normalized |
|-------|-----------|
| `+15555555555` | `+15555555555` (unchanged) |
| `5555555555` | `+15555555555` (10-digit NA -> +1 prefix) |
| `15555555555` | `+15555555555` (11-digit starting with 1) |
| `864674` | `+864674` (short codes preserved) |

## Live Sync (continuous import)

Beyond the manual wizard, the module can ingest backups continuously through two
scheduled crons. **Both ship inactive by default** — they run against
deployment-specific paths and credentials, so the operator must configure them
first, then activate the crons under **Settings > Technical > Scheduled Actions**.

### Nextcloud folder watcher (`ir_cron_sms_nc_watch`, every 15 min)

Polls a Nextcloud folder over WebDAV for new `*.xml` / `*.zip` exports, imports
them, then files each processed source into a `done/`, `failed/`, or
`too_large/` subfolder. Files larger than 200 MB are stream-downloaded and
auto-split into ~80 MB chunks before per-chunk import (resume-safe; hash dedup
keeps it idempotent).

Nextcloud credentials are read from the environment:
`NC_SMS_WATCH_URL`, `NC_SMS_WATCH_USER`, `NC_SMS_WATCH_PASSWORD`, falling back to
`NC_URL`, `NC_USER`, `NC_PASSWORD`.

### Disk inbox import (`ir_cron_sms_disk_import`, every 10 min)

Imports `*.xml` / `*.zip` files dropped into an on-host bind-mount inbox
(set by `ir.config_parameter` `bf_sms_archive.disk_inbox_path`, default
`/mnt/sms-inbox`, mapped via your `docker-compose` volume). This path
bypasses the 1 GB form-upload limit — copy large exports in via `scp`. Processed
files are moved to `done/` (success) or `failed/` (errors).

### Configuration parameters

| Key | Default | Role |
|-----|---------|------|
| `bf_sms_archive.nc_watch_path` | *(empty placeholder)* | Nextcloud folder to watch (e.g. `/Backups/SMS/Live`). **Must be set** before activating the watch cron; an empty value makes the cron a no-op. |
| `bf_sms_archive.nc_watch_user_id` | `1` (placeholder) | Odoo user ID that will own messages imported by the watch cron. Set to the intended internal user. |

## Live Android sync (v18.0.2.0.0+)

The companion app **bf-sms-relay** (separate repo) pushes SMS, MMS, and call log entries to this module continuously, replacing manual XML import.

### Endpoint

```
POST <odoo_base_url>/bf_sms_archive/api/ingest
Authorization: Bearer <device_token>
Content-Type: application/json
```

Limits: `entries: <= 1000`, payload `<= 50 MB`.

### Authentication

Each Android device is represented by a `sms.archive.device` record (manager-only). The record carries:

- `name` — human label (e.g. "Pixel 8")
- `owner_id` — the `res.users` to whom received entries are imputed
- `api_token` — opaque 32-byte urlsafe secret; regenerated via the "Régénérer le token" button (immediately invalidates the previous one)
- `last_sync_at`, `last_sync_count`, `total_received` — server-side stats

The token is stored on a field with `groups="bf_sms_archive.group_sms_manager"` so only managers can read it.

### Payload format

```json
{
  "device_id": "pixel-8",
  "entries": [
    {
      "kind": "sms",
      "phone": "+15145551234",
      "direction": "in",
      "body": "Hello",
      "date_ms": "1731234567890",
      "contact_name": "Marc Lemay",
      "is_mms": false
    },
    {
      "kind": "sms",
      "phone": "+15145551234",
      "direction": "out",
      "body": "Photo!",
      "date_ms": "1731234600000",
      "is_mms": true,
      "parts": [
        {"content_type": "image/jpeg", "filename": "IMG_001.jpg", "data_b64": "/9j/4AAQ..."},
        {"content_type": "text/plain", "text": "Photo!"}
      ]
    },
    {
      "kind": "call",
      "phone": "+15145551234",
      "call_type": "outgoing",
      "duration": 125,
      "date_ms": "1731234999000",
      "presentation": "allowed"
    }
  ]
}
```

`kind` accepts `sms` or `call`. SMS `direction` is `in`/`out`/`draft`. Call `call_type` is one of `incoming`, `outgoing`, `missed`, `voicemail`, `rejected`, `blocked`.

### Response

```json
{
  "created": 7,
  "duplicates": 2,
  "errors": 0,
  "error_details": []
}
```

Status codes: `200` on success (even if some entries failed — see `errors`), `400` on malformed payload, `401` on bad token, `413` on oversize payload.

### Dedup

The same SHA-256 hashing used by the XML import is applied — re-posting the same entry is safe and counts as a duplicate. The hash key is:

- SMS: `phone_normalized | date_ms | body`
- Call: `phone_normalized | date_ms | duration | call_type`

### Smoke test

```bash
TOKEN=...
curl -X POST https://odoo.example.com/bf_sms_archive/api/ingest \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"entries":[{"kind":"sms","phone":"+15145551234","direction":"in","body":"smoke test","date_ms":"1731234567890"}]}'
```

### Companion Android app

`bf-sms-relay` (LGPL-3, F-Droid-friendly): foreground service when charging (30s tick), WorkManager when on battery (15 min tick), BroadcastReceiver for incoming SMS (real-time). Distribution: signed APK direct, not Play Store (READ_SMS / READ_CALL_LOG are blocked by Play policy).

## Changelog

### Version 18.0.5.12.0

- **SECURITY:** Device pairing now requires **PKCE** (`code_challenge` +
  `code_challenge_method=S256` on `/auth/start`, `code_verifier` on the
  exchange). A custom app scheme is not exclusive on Android: another
  application can declare the same scheme and receive the pairing code instead
  of yours. Without PKCE that intercepted code would be exchanged for a bearer
  token — that is, for someone's whole SMS mailbox. With it, a code intercepted
  from someone else's pairing is worth nothing: the exchange demands a verifier
  that never left the app that started that pairing, and the pending row is
  destroyed on a wrong one rather than left open to a second try.
  The redirect-scheme allowlist does not cover this: it closes the open
  redirect **server-side**, not the local interception of the code on the
  device. No grace period, deliberately: already-paired devices keep their
  token and never go through the exchange again, so only a *new* pairing
  started from an older APK breaks — loudly, with a readable reason.
- **FIX:** Every VOIP.ms API call now sends an explicit `User-Agent`. The
  provider answers `403` to `requests`' default one **before** checking
  credentials, so the same 403 comes back with a bogus password and with a
  user that does not exist. Without the header every call failed and the
  archive stopped in silence — it did, for seven weeks, before anyone noticed.
- **NEW:** `bf_sms_archive.push_enabled` (default `1`) switches native push
  off without uninstalling anything, mirroring the email side's own switch.
- **NEW:** `bf_assetlinks.extra_statements` carries additional Android
  Digital Asset Links statements as raw JSON. Only one route in an Odoo
  installation can serve `/.well-known/assetlinks.json`, so a second app has
  to go through this one; a setting beats a deployment. The content is checked
  for shape before being served — it must parse, and be an object or a list of
  objects — and anything else is logged and dropped rather than left to make
  the whole file unreadable to Android, which would silently cost the
  **existing** app its link verification. The check is structural, not
  semantic: a well-formed object that is not a valid statement still reaches
  the file.

### Version 18.0.5.11.1

- **NEW:** `POST /send` on the mobile API accepts `media` -- a list of `{filename, content_type, data_b64}` -- and hands it to `action_send`, which already knew how to turn it into a `sendMMS` call. Outbound MMS was reachable from the web messenger and from nowhere else; the phone could only send text. This is what lets the Android app receive a photo from the system share sheet and send it on as an MMS.
- **GUARD:** Attachments are validated before `action_send` sees them: at most three parts (the provider exposes `media1`..`media3`, and a fourth would be dropped **silently** -- message created, marked sent, one photo missing at the other end), 1 MB per part, 2 MB in total, base64 that actually decodes, a MIME type matching `type/subtype` before it reaches a `data:` URI, and a filename stripped of path separators.
- **GUARD:** An MMS on a line with `mms_enabled = False` is refused with a 400 before anything is created. `action_send` does ask the question, but inside its own `try`, so the `UserError` was caught, the message created anyway in `failed` state, and the route answered `ok` -- the caller would move on to the thread with a photo that looks sent and never left the device.
- **CHANGE:** A send with neither body nor attachment is refused (`empty_message`) instead of dispatching an empty SMS. A send with an attachment and no body is fine: a photo on its own is a whole message.
- **TESTS:** Nine unit tests on the attachment guard, six HTTP tests covering the send path end to end.

### Version 18.0.5.10.0

- **NEW:** Linking a conversation to a contact now writes the thread's number into that contact's **Mobile** field. Until now the link only taught the messenger a name: the number itself stayed unknown to the contact record, so it could not be found from anywhere else in Odoo and the next thread opened on it did not match on its own.
- **NEW:** A number already on file is recognised whatever its formatting -- `(514) 555-0101`, `514-555-0101` and `+15145550101` compare equal on their last ten digits, in `mobile` and in `phone` alike -- so an existing number is never duplicated.
- **NEW:** A contact whose Mobile already holds a *different* number keeps it. The thread's number goes to the contact's chatter as a note instead, for a human to arbitrate.
- **NEW:** Every write-back leaves a note in the contact's chatter, and the messenger raises a toast when it happens -- writing into someone's contact record should not be silent.
- **CHANGE:** Creating a contact from an unknown number fills `mobile` instead of `phone`: a number that exchanges text messages is a mobile.
- **CHANGE:** The write-back is a service, never a condition. It runs inside a savepoint and is skipped -- link intact, one log line -- when the user lacks write access to contacts or another module refuses the write.
- **FIX:** `views/project_task_views.xml` is declared in the manifest. The file shipped from 18.0.5.8.0 but was never loaded, so the **Conversations** stat button was missing from the task form.

### Version 18.0.5.9.0

- **NEW:** Shared lines -- `sms.archive.line.user_ids` lets several staff work one number. Shared users send from it and read the messages held on it; record rules on threads, messages and MMS parts were widened accordingly, and line write access stays with the owner.
- **NEW:** `sms.archive.thread.line_id` records the line a conversation opened on. It decides who the thread is shared with, and never switches on its own when a reply goes out on another line.
- **NEW:** Message origin in the messenger -- each bubble names its line (and, on a shared line, the colleague who wrote it) as soon as the instance has more than one number. New `sms.archive.message.sent_by_id` records who composed an outgoing message.
- **NEW:** Reply-line guard in the composer -- warns when the selected send number is not the one the last exchange used, with a one-click switch.
- **SEC:** Message access is scoped by the message's own line, not by its thread. A thread mixes lines, so thread-level scoping would have handed a colleague the owner's whole imported history the first time an archived contact texted the shared number. The call log stays owner-only for the same reason, and the dashboard remains the owner's view.
- **SEC:** `sms.archive.thread.write()` refuses `owner_id`, `line_id` and `is_hidden` from a shared reader -- write access on a shared thread is for marking read, pinning and archiving, not for taking it over.
- **CHANGE:** The messenger recomputes preview and unread counts under the reader's own rules, and tells a shared reader how many messages of the thread are held elsewhere. A shared line never becomes someone else's default send number.
- **MIGRATION:** `post-migrate` backfills `thread.line_id` from the most recent message carrying a line. Threads with no live message (imported archives) stay unlinked and owner-only.

### Version 18.0.5.5.5 (catch-up entry covering 18.0.5.5.3 – 18.0.5.5.5)

- **FIX:** GSM-7 segments are now budgeted in **UTF-8 bytes**, not characters. The VOIP.ms `sendSMS` length check counts bytes, so GSM-7 accented letters (`é è à ù ì ò ä ö ñ ü`…) cost 2 even though they fit in a single septet — a full 160-character segment containing a single accent (161 bytes) was rejected with `sms_toolong`, failing the whole message while shorter UCS-2 messages went through. Each character now costs `max(GSM-7 septets, UTF-8 bytes)`; pure-ASCII segments keep the full 160-character budget, and messages with accents split slightly earlier instead of being refused.
- **SEC:** The push-subscription endpoint registered from the mobile client is now validated with the module's anti-SSRF guard (public scheme + public address) before the server ever POSTs to it, and outbound media fetches pin the connection to a single resolved public IP, closing the DNS-rebind window between the safety check and the actual request.

### Version 18.0.3.7.0

- **FIX:** Outbound SMS no longer fail with the VOIP.ms `sms_toolong` error on messages that contain non-GSM-7 characters. Segmentation is now encoding-aware: 160 septets per SMS for GSM-7 text (the extension characters `^{}\[~]|€` counted as two), and 70 characters per SMS as soon as the body contains any character that forces UCS-2 encoding (lowercase `ç`, `œ`, curly quotes, `…`, accented capitals, emoji…). Every segment now fits inside a single SMS envelope, so long or accented messages are delivered instead of rejected.
- **NEW:** Smart MMS escalation — a message that would need more than one SMS is sent as a single MMS (on MMS-enabled lines) instead of several fragmented texts, with automatic fallback to segmented SMS if the MMS is refused. The threshold is configurable via the system parameter `bf_sms_archive.mms_escalation_min_segments` (default `2`; set below `2` to always split into SMS instead).
- **NEW:** The composer character counter is encoding-aware — it flags Unicode bodies (70 chars/SMS) and shows whether the message will go out as one MMS or as N SMS.
- **FIX:** The "Messagerie" chat workspace now honours the optional Symbifox dark mode (`bf_dark_mode`) — the conversation canvas, sidebar, message bubbles, composer and attachment chips adopt the dark palette instead of remaining light.

### Version 18.0.3.6.3

- **FIX:** The VOIP.ms transport enable flag (`bf_sms_archive.voipms_enabled`) is now read tolerantly (`1`/`true`/`yes`/`on`). A Boolean settings toggle is stored by Odoo as `"True"`/`"False"`, so the previous strict `== "1"` check silently disabled the transport whenever the Settings form was saved.

### Version 18.0.3.6.1

- **NEW (3.0+):** Live two-way SMS/MMS messaging over VOIP.ms — a built-in OWL "Messagerie" chat workspace (thread list + conversation), a systray unread badge, an autonomous VOIP.ms transport (`sms.archive.voipms`), a per-line inbound webhook (`/bf_sms_archive/api/voipms/sms?token=`), and a safety-net poll cron.
- **NEW:** Per-line DID inventory (`sms.archive.line`) with owner mapping, SMS/MMS capability flags and a regenerable webhook token; outbound send with MMS attachments, character/segment counter and per-thread draft persistence.
- **NEW:** Simple/Advanced display toggle in the messenger — a decluttered default (overflow `⋯` menus) or the full legacy toolbars.
- **NEW:** Settings field for the VOIP.ms account timezone (`bf_sms_archive.voipms_tz`, US/Eastern by default).
- **FIX:** Inbound/outbound timestamps now interpret VOIP.ms times in the account timezone (DST-aware) instead of UTC, so message times are correct in every viewer's timezone.
- **FIX:** Opening a conversation scrolls to the latest message; the systray badge clears in real time when messages are read.
- **CHANGE:** App renamed from "Archive SMS & Appels" to "SMS & Calls".

### Version 18.0.2.2.1

- **CHANGE:** Live-sync crons (`ir_cron_sms_nc_watch`, `ir_cron_sms_disk_import`) now ship **inactive** — activate them after configuring paths and credentials
- **CHANGE:** `nc_watch_path` and `nc_watch_user_id` reset to placeholder defaults; the operator must set them before use
- **DOC:** Documented the live-sync crons, their configuration parameters, and the `requests` Python dependency; added a `LICENSE` file

### Version 18.0.2.0.0

- **NEW:** Public REST endpoint `/bf_sms_archive/api/ingest` for live SMS/MMS/call ingestion from the companion Android app `bf-sms-relay`
- **NEW:** `sms.archive.device` model with per-device Bearer tokens (manager-only field, regenerable)
- **NEW:** `sms.archive.thread._get_or_create()`, `sms.archive.message._ingest_one()`, `sms.archive.mms.part._ingest_parts()`, `call.archive.call._ingest_one()` helper methods reusable by future ingestion sources
- **NEW:** Configuration > Appareils Android menu (manager group only)
- **NEW:** Nextcloud folder watcher ("live sync") -- `_cron_nc_watch_import` polls a configured Nextcloud folder over WebDAV and imports new XML/ZIP exports automatically, sorting processed sources into `done/`, `failed/`, and `too_large/`
- **NEW:** Nextcloud credentials read from the environment (`NC_SMS_WATCH_URL/USER/PASSWORD`, falling back to `NC_URL/USER/PASSWORD`)
- **NEW:** Config parameters `bf_sms_archive.nc_watch_path` and `bf_sms_archive.nc_watch_user_id`
- **NEW:** `requests` added to `external_dependencies`

### Version 18.0.1.5.0

- **NEW:** Disk-import cron (`ir_cron_sms_disk_import`) -- polls an on-host bind-mount inbox (`/mnt/sms-inbox`) every 10 minutes and imports XML/ZIP dropped there via `scp`, bypassing the 1 GB form-upload limit; new import button in the wizard
- **NEW:** Auto-split of oversized XML -- files larger than 200 MB are stream-downloaded and split into ~80 MB chunks (one complete `<smses>`/`<calls>` document each), imported chunk by chunk with per-chunk commit (resume-safe); hash dedup keeps it idempotent

### Version 18.0.1.3.1

- **CHANGE:** Threads ordered by `last_message_date desc nulls last, id desc` so threads with no recorded last-message date sort to the bottom of the list

### Version 18.0.1.3.0

- **NEW:** `recording_url` field on `call.archive.call` -- Nextcloud internal link to audio file for calls sourced from the VOIP.ms daily sync (from the VOIP.ms daily recording sync)
- **NEW:** Recording link shown in the call tree view (optional column) and on the call form
- **CHANGE:** Call records imported by VOIP.ms sync use `import_batch_id` prefixed with `voipms-` to distinguish from SMS Backup & Restore imports

### Version 18.0.1.2.0

- **NEW:** Call log import -- parses `calls-*.xml` from SMS Backup & Restore
- **NEW:** `call.archive.call` model with type, duration, presentation fields
- **NEW:** Auto-detection of SMS vs call log XML format in the import wizard
- **NEW:** Multi-file ZIP support -- import both SMS and call logs from a single ZIP
- **NEW:** Journal d'appels menu with list, form, search, graph, and pivot views
- **NEW:** Calls tab in thread form view with colour-coded type badges
- **NEW:** Call stat button (`fa-phone`) on thread form
- **NEW:** 3 MCP tools: `call_list_calls`, `call_get_calls_for_thread`, `call_search_calls`
- **NEW:** Chunked import script supports `calls-*.xml` files
- **NEW:** OWL dashboard with KPIs, monthly trends, top 10 contacts, call statistics
- **NEW:** Date range filter on dashboard (3m, 6m, 1y, 2y, all, custom)
- **NEW:** Dashboard as module landing page (sequence 1)
- **CHANGE:** App renamed from "Archive SMS" to "Archive SMS & Appels"
- **CHANGE:** `sms_import_backup` MCP tool now auto-detects and handles call logs

### Version 18.0.1.1.0

- **NEW:** PDF export with branded chat bubbles, company logo and name
- **NEW:** MMS support -- `sms.archive.mms.part` model for image/media attachments
- **NEW:** HEIC/WEBP -> JPEG conversion for PDF rendering (via Pillow)
- **NEW:** Emoji BMP mapping for wkhtmltopdf compatibility
- **NEW:** CSV and XML export actions
- **CHANGE:** Brand colours read from `res.company` (bf_lexend soft dependency)
- **CHANGE:** `bf_lexend` moved from hard to soft dependency

### Version 18.0.1.0.0

- **NEW:** Initial release -- Phase 1 MVP
- SMS + MMS import from SMS Backup & Restore XML/ZIP
- Thread and message models with owner-only security
- Confidential thread hiding (`is_hidden`)
- Auto-match contacts by phone number
- Post messages and calls onto any chatter-bearing record
- 6 MCP tools for search, browse, import, and task linking
- Full deduplication via SHA-256 hashing

## License

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-08-12, this version converts automatically to
  **LGPL-3.0-or-later**.

---

<sub>Authored and maintained by Les services de consultation Blue Fox, Inc. AI coding assistants were used as productivity tools during development.</sub>

## Third-party assets

`static/fonts/NotoEmoji-Regular.ttf` is Noto Emoji, Copyright 2013 Google LLC, under the SIL Open Font License 1.1. See [`THIRD-PARTY.md`](THIRD-PARTY.md) for the full notice, including the fact that this is a static instance of the upstream variable font.
