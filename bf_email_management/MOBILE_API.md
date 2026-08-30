# Mobile mail API — contract

Consumed by the **Odoo Inbox** Android app (the mail half of the two-tab
app; the client is not part of this repository).

Base URL: `<instance>/bf_email_management/mobile/v1`
All bodies JSON. Auth: `Authorization: Bearer <token>` on every call except
`/ping`, `/auth/start` and `/auth/exchange`.
Any auth failure → HTTP 401 `{"error":"unauthorized"}`.
Any malformed parameter → HTTP 400 `{"error":"<message>"}` — never a 500.

Sibling API: `bf_sms_archive` serves the SMS tab at
`<instance>/bf_sms_archive/mobile/v1`. The two are independent — separate
tokens, separate push registrations, separate installs.

---

## Discovery

### `GET /ping` (public)
```json
{"ok": true, "module": "bf_email_management", "api": 1, "version": "18.0.6.8.0"}
```
The app probes both module APIs at instance-setup time to decide which tabs to
show. A connection error or non-200 means "this half isn't installed" — hide
the tab, don't fail the login.

---

## Auth — web-login capture

The app collects **no password**. It opens the Odoo web login in a Chrome
Custom Tab and captures a one-time code, then trades it for the durable token.
This inherits everything `/web/login` offers: password, SSO (Authentik,
SAML, OAuth…), MFA.

There is no `/login` route. Deliberately — see `models/bf_email_mobile_device.py`.

### Two-tab login: chain the legs in one browser session

1. App generates a random `state`, opens a Custom Tab to
   `<instance>/bf_sms_archive/mobile/v1/auth/start?redirect=<scheme>://auth&state=<state>`.
2. Deep link comes back with `code` (or `error`). App exchanges it.
3. App **immediately** opens the second leg in the same Custom Tab:
   `<instance>/bf_email_management/mobile/v1/auth/start?redirect=<scheme>://auth&state=<state2>`.
   The Odoo session cookie is already set, so `auth="user"` does **not** prompt
   again — the user sees one login, the app ends up with two tokens.
4. Store both tokens (EncryptedSharedPreferences), keyed by service.

A leg that comes back with `error=` disables that tab; it must not abort the
other leg.

### `GET /auth/start?redirect=&state=&device_name=` (auth=user)
Redirects (302) to `<redirect>?code=<one-time>&state=<state>`, or
`<redirect>?error=<reason>&state=<state>`.

| `error` | meaning |
|---|---|
| `no_access` | not an internal user |
| `no_mailbox` | no active `bf.email.account` — nothing to show |

A `redirect` not matching `bf_email_management.mobile_redirect_schemes` gets a
plain **400**, not a bounce: refusing to redirect is the whole point.

Codes are single-use with a 3-minute TTL.

> Werkzeug normalizes the Location header, so `redirect=odooinbox://auth` comes
> back as `odooinbox://auth/?code=…` — with a trailing slash on the path. The
> intent-filter must therefore declare `scheme` + `host` and **no** `android:path`,
> which matches any path. (This is what `bf_sms_archive`'s app already does; a
> `path="/auth"` constraint added later would silently stop matching.)

### `POST /auth/exchange`
Body: `{"code": "..."}`
```json
{"token": "...", "user_id": 2, "config": Config}
```
401 `{"error":"invalid_or_expired_code"}`.

### `POST /logout` (auth)
Deactivates the device and clears its push endpoint. → `{"ok": true}`

---

## Bootstrap

### `GET /config` (auth) → `Config`
```jsonc
{
  "user_name": "Jane Doe",
  "tz": "America/Montreal",
  "signature": "<p>…</p>",
  "accounts": [{"id": 1, "name": "Work", "login": "jane@example.com",
                "aliases": "", "state": "connected"}],
  "counts": {"inbox": 12, "unread": 3, "snoozed": 2, "unrouted": 5},
  "snooze_presets": [{"key": "tonight", "label": "Ce soir (18 h)",
                      "until_ms": 1786831200000}],
  "routable_models": [{"model": "project.task", "label": "Tâche"}],
  "spawn_kinds": ["task", "ticket", "lead", "bill", "invoice", "expense"]
}
```
`accounts` carries addressing only — never host/login/password.
`routable_models` and `spawn_kinds` are filtered to what this instance actually
has installed and the user may read, so the app can build its menus from the
response instead of hardcoding Odoo apps.
`snooze_presets` are already resolved in the user's timezone.

---

## Reading

### `GET /threads?filter=&search=&account_id=&offset=&limit=` (auth)
`filter` ∈ `inbox` (default) · `unread` · `snoozed` · `handled` · `sent` ·
`unrouted` · `all`. Unknown filter → 400.
`limit` defaults to 25, clamped to 100.

```json
{"threads": [Thread], "has_more": true}
```
Newest activity first. One entry per conversation.

### `GET /conversation?thread_key=&load_images=` (auth)
Every message of a thread, oldest → newest. Threads longer than 60 messages are
truncated from the **old** end (`"truncated": true`).

The **last** message comes back `full` (body, headers, attachments) — opening a
thread to read what just arrived is the common case and shouldn't cost a second
round-trip. The others are previews; fetch `/message` when the user expands one.

```json
{"thread_key": "<root@x>", "subject": "…", "messages": [Message], "truncated": false}
```

### `GET /message?id=&load_images=` (auth) → `Message` (full)
Marks the message read as a side effect; the returned `status` already
reflects that.

### `POST /attachment/upload` (auth, multipart)
Stage one outbound file. Field name `file`; 25 MB per file, 25 MB total per send.
```json
{"ok": true, "attachment_id": 509, "name": "devis.pdf", "size": 84213, "mimetype": "application/pdf"}
```
400 `missing_file` / `empty_file`, 413 `too_large`.

Pass the ids to `/reply` or `/compose` as `attachment_ids`. They are **single-use
and device-scoped**: an id is only accepted from the device and user that
staged it, and only once — sending consumes it. Anything else, including a
valid `ir.attachment` id the user never uploaded, comes back
`Pièce jointe inconnue ou déjà envoyée.` That check is what stops `/reply`
from being a one-request export of every document in the database.

Unclaimed uploads are swept after 24 h.

### `GET /attachment?email_id=&idx=` (auth)
Streams the bytes. `idx` is the position in that message's own `attachments`
array — **not** an `ir.attachment` id. 404 when out of range, 413 past 25 MB.
`Cache-Control: private, no-store`.

---

## Counts

### `GET /counts?grouped=` (auth)
```json
{"counts": {"inbox": 5, "unread": 2, "snoozed": 1, "unrouted": 0}}
```
The badge numbers on their own, without a page of mail — cheap enough to re-read
on every pull-to-refresh, which is what the app does.

⚠️ **Counts follow `grouped`, because they count what the list *shows*.** With
folding on (the default) a conversation is one row and therefore counts once;
`grouped=0` counts messages, matching the flat view it serves. Sending the flag
on `/threads` but not here is what produced "Inbox · 6" over five rows.

The same flag is accepted on the three triage routes below, so the numbers they
return match the view the app is currently in.

---

## Triage

All three return `{"ok": true, "counts": {…}}` with counts **already reflecting
the write** (the server flushes before recounting), so the app can set its
badges from the response instead of refetching `/config`.

| route | body |
|---|---|
| `POST /mark_read` | `{"email_ids": [12, 13], "grouped": true}` |
| `POST /handle` | `{"email_ids": [12], "handled": true, "grouped": true}` |
| `POST /snooze` | `{"email_ids": [12], "until_ms": 1786831200000, "grouped": true}` |

`grouped` is optional and defaults to `true`, so a client written before this
route existed keeps the behaviour it was built against.

`handled: true` runs the real archive path: the message moves to
`Archives/{YYYY}` **on the IMAP server** (per `bf.email.account.writeback_archive`)
and the row's open reminder activities are closed. `handled: false` restores it.
A snooze in the past → 400.

An IMAP write-back that fails (server unreachable) is logged and does **not**
fail the request — the Odoo-side state still moved.

---

## Sending

### `POST /reply` (auth)
```jsonc
{"email_id": 12, "mode": "reply",       // reply | reply_all | forward
 "body": "texte brut, \n\n = paragraphe",
 "to": ["a@b.c"],            // optional; REQUIRED for forward
 "cc": ["d@e.f"],            // optional
 "attachment_ids": [509],    // optional; from /attachment/upload
 "client_token": "uuid"}     // optional; see "Sending twice" below
```
→ `{"ok": true, "email_id": 12, "thread_key": "…"}`

Recipients default to what the desktop buttons compute: reply → original
sender; reply_all → sender in To, other thread participants in Cc minus your
own addresses and the tenant's catchall/bounce aliases. Forward has no default
recipient by design, so `to` is mandatory there.

The quoted original and your Odoo signature are appended server-side — send
only what the user typed. A genuine reply to an inbound message flips its
status to `replied`, same as the desktop composer.

### `POST /compose` (auth)
```jsonc
{"to": ["a@b.c"], "cc": [], "subject": "…", "body": "…",
 "res_model": "project.task", "res_id": 42,   // both optional
 "attachment_ids": [509],                     // optional
 "client_token": "uuid"}                      // optional
```
→ `{"ok": true}`

With `res_model`/`res_id`, the message is posted on that record's chatter
(allowlisted models only). Without, it lands on the **first recipient's**
contact card.

---

### Sending twice — `client_token`

A client that composes offline and replays on reconnect cannot distinguish
"the send failed" from "the send succeeded and the response was lost". Retrying
the second case puts a duplicate in the correspondent's inbox.

So generate a token **before the first attempt**, reuse it on every replay, and
the server turns the repeat into a no-op:

```json
{"ok": true, "duplicate": true}
```

Enforced by a unique index, not a read-then-write. Tokens are remembered 30
days. Omit the field and no deduplication happens — it is opt-in.

## Odoo-side actions

### `GET /records?model=&q=&limit=` (auth)
Routing targets. `model` must be in the allowlist (`routable_models` from
`/config`); `q` needs ≥ 2 characters.
→ `{"records": [{"id": 42, "name": "…"}]}`

### `POST /route` (auth)
`{"email_id": 12, "res_model": "project.task", "res_id": 42}`
Imports the email into that record's chatter (the desktop "reroute" verb).
→ `{"ok": true, "record": {"model": "…", "id": 42, "name": "…"}}`

### `POST /spawn` (auth)
`{"email_id": 12, "kind": "task"}` — `kind` ∈ `spawn_kinds` from `/config`.
Creates the record, imports the email into its chatter, and marks the email
handled. → `{"ok": true, "record": {…}}`

Some kinds open a pre-filled form on the desktop rather than committing a
record; those come back as a 400 telling the user to finish in Odoo, instead of
reporting a success they won't find anywhere.

---

## Push — UnifiedPush / ntfy (no Google)

The app registers **one** UnifiedPush endpoint with the phone's ntfy app, then
POSTs it to **both** module APIs. Both publish to it independently; payloads are
told apart by `type`.

### `POST /register_push` (auth)
`{"endpoint": "https://ntfy.example.com/upXXXX", "app_version": "2.0.0"}`
Endpoints resolving to a private/loopback/link-local address are refused (400
`invalid_endpoint`) — the server POSTs to this URL from a cron, so it would
otherwise be a blind-SSRF sink. Re-checked at send time too.

Payloads:
```jsonc
{"type": "mail", "title": "Acme inc.", "body": "Objet du courriel",
 "preview": "…", "email_id": 12, "thread_key": "<root@x>", "account_id": 1}
{"type": "mail_clear", "email_id": 12}   // read/handled elsewhere → drop the notif
{"type": "mail_clear_all"}
```
Past 5 new messages in one sync, a single summary push is sent with
`email_id: false` — tapping it should open the inbox, not a message.

Server-side config (Odoo ▸ Technical ▸ System Parameters):
`bf_email_management.ntfy_publish_token`, `bf_email_management.mobile_redirect_schemes`.
Empty token = push disabled; the app still works by pull.

---

## Types

```jsonc
Thread = Message + {
  "thread_key": "<root@x>" | "id:42",
  "last_id": 12, "message_count": 4, "unread_count": 1,
  "last_date_ms": 1786831200000
}

Message = {
  "id": 12, "thread_key": "…", "direction": "in" | "out",
  "subject": "…", "from": "Nom <a@b.c>", "from_label": "Acme inc.",
  "date_ms": 1786831200000, "preview": "160 chars",
  "status": "new" | "read" | "replied", "is_handled": false,
  "snoozed_until_ms": false, "category": "…", "priority": "0",
  "has_attachments": false, "attachment_count": 0,
  "partner_id": 7, "partner_name": "Acme inc.", "account_id": 1,
  "record": {"model": "project.task", "id": 42, "name": "…"} | false,
  "is_question": false, "is_action_request": false,

  // full payloads only (last message of a conversation, or /message)
  "to": "…", "cc": "…",
  "body_html": "sanitized, remote <img> parked in data-blocked-src",
  "blocked_images": 2,
  "attachments": [{"idx": 0, "name": "rapport.csv", "mimetype": "text/csv",
                   "size": 24, "attachment_id": false}],
  "message_id_header": "<msg@x>"
}
```

`body_html` is meant for a WebView. Remote images are blocked until the reader
asks (`load_images=1`), and `blocked_images` is how many were parked — show the
"load images" bar only when it's non-zero.
In full payloads `attachment_count` is the number actually downloadable now,
which can be lower than the ingest-time count if the raw message left the
filestore.
