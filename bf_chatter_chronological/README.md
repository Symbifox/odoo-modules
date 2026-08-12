# BF Chatter Chronological

Odoo 18 Community module that sorts the chatter feed by the original email `Date` header instead of by insertion `id`. Imported emails land at their true chronological position, regardless of when they were posted to the record.

## Use case

The default Odoo 18 chatter sorts `mail.message` records by `id desc`, which corresponds to insertion order. When emails are imported retroactively — from an IMAP archive, a forwarded `.eml`, a vendor receipt batch — they get the highest available `id` and float to the top of the feed, even when their actual `Date:` header is months old.

Users reading the chatter to reconstruct a conversation see the messages in import-order rather than time-order: a January receipt appears above an April reply, simply because it was imported later. This breaks the "scroll up to read history" mental model and hides the real timeline.

This module makes the chatter follow `mail.message.date` (which holds the email's original `Date` header when set by the importer) as the primary sort key, with `id` as a tie-break. Existing real-time messages — where `date ≈ create_date` — are unaffected.

## Features

- **Server `_order` override** — `mail.message._order = 'date desc, id desc'`. Any `search`/`search_read` without an explicit `order=` now follows the chronological convention.
- **Date-aware pagination** — `_message_fetch` translates the chatter's `before` / `after` id-cursors to a compound `(date, id)` cursor, so "Load more" fetches truly older messages by date rather than by id.
- **Front-end re-sort** — patches `Thread.fetchMessages` / `fetchMoreMessages` / `fetchNewMessages` on `@mail/core/common/thread_model` to apply a final date-based sort after each load, defeating the JS's id-only sort.
- **On-demand per-record backfill** — a cogwheel ⚙️ action **« Réordonner ce chatter par date »** scans the current record's messages where `date ≈ create_date` and tries to recover the original `Date:` from the quoted-reply block in the stored body. Notification reports `N message(s) re-daté(s)`.
- **Composite index** — `_auto_init` creates `mail_message_model_res_id_date_id_idx` on `(model, res_id, date desc, id desc) WHERE model IS NOT NULL AND res_id IS NOT NULL`, aligned with the new ordering to prevent sequential scans on records with hundreds of messages.

## Technical architecture

### Structure

```
bf_chatter_chronological/
├── __init__.py
├── __manifest__.py
├── README.md
├── data/
│   └── actions.xml          # cogwheel server actions per model
├── models/
│   ├── __init__.py
│   └── mail_message.py      # _order + _message_fetch + backfill action + index
└── static/
    └── src/
        └── thread_model_patch.js  # JS re-sort after each fetch
```

### Dependencies

| Module | Role |
|--------|------|
| `mail` | base `mail.message` model + Owl `Thread` model being patched |
| `project` | chatter-bearing records covered by the re-sort |
| `crm` | chatter-bearing records covered by the re-sort |
| `account` | chatter-bearing records covered by the re-sort |
| `hr_expense` | chatter-bearing records covered by the re-sort |
| `helpdesk_mgmt` | chatter-bearing records covered by the re-sort |
| `bf_meeting` | chatter-bearing records covered by the re-sort |

No external Python libraries (regex parsing of quoted Date headers uses `email.utils.parsedate_to_datetime` from the stdlib).

### Backend

`mail.message` is inherited with:

- `_order = 'date desc, id desc'`
- `_auto_init()` creating the composite index via `CREATE INDEX IF NOT EXISTS`
- `_cursor_domain(msg, comparator)` — static helper building leaf domains expressing `(date, id) <comparator> (msg.date, msg.id)` for `<`, `<=`, `>`
- `_message_fetch(domain, ..., before=, after=, around=, limit=)` — mirrors upstream signature but translates id cursors via `_cursor_domain`, returns `mail.message` recordsets sorted by the compound key
- `action_backfill_chatter_dates()` — reads `active_model` / `active_id` from context, scans messages where `date ≈ create_date` (within 60 s), attempts to parse the quoted-reply `Date:` / `Sent:` / `Envoyé le:` header via two regex patterns, writes the parsed value back with a notification-suppressing context (`mail_create_nolog`, `tracking_disable`, etc.). Snapshots `mail.mail.search_count` before/after as a side-effect guard.

The action returns an `ir.actions.client` `display_notification` payload summarizing the count of re-dated, already-backdated, examined, and unparseable messages.

### Frontend

The Owl `Thread` model (`@mail/core/common/thread_model`) is patched via `@web/core/utils/patch`:

- `fetchMessages(opts)` — calls `super()`, then `messages.sort(compareByDateAsc)` for the initial load
- `fetchMoreMessages(epoch)` — calls `super()`, then re-sorts (older or newer scroll)
- `fetchNewMessages()` — calls `super()`, then re-sorts (periodic / focus refresh)

`compareByDateAsc` uses `@mail/utils/common/misc:compareDatetime` for Luxon comparison and falls back to `id` on equal dates. The array is left in oldest-first order — the chatter template is responsible for visual reverse, which matches the upstream contract.

### Server actions (cogwheel bindings)

Eight `ir.actions.server` records bind « Réordonner ce chatter par date » to: `project.task`, `helpdesk_mgmt.helpdesk.ticket`, `crm.lead`, `res.partner`, `account.move`, `hr.expense`, `bf_meeting.meeting.record`, `bf_meeting.meeting.agenda`. Each is a single-line code action: `action = env['mail.message'].action_backfill_chatter_dates()`.

To extend to a new model, duplicate one of the `<record>` elements in `data/actions.xml` and update the id + `binding_model_id` ref.

### Security review

Pre-publication checklist (per BF policy):

| Concern | Status |
|---------|--------|
| Hardcoded secrets | None |
| SSRF | No outbound HTTP |
| Command injection | No `subprocess`, no shell |
| SQL injection | Domains built via Odoo `expression`; raw SQL is one `CREATE INDEX IF NOT EXISTS` with no interpolation |
| ReDoS | Two regex patterns on body content; anchored, bounded character classes; no catastrophic alternation |
| ACL coverage | Inherits `mail.message`; no new model. Backfill uses `sudo()` to write the `date` field — by design, since the operator (form cogwheel) may not own the record but is authorized to view it |
| `sudo()` usage | Justified: rewriting `mail.message.date` on records the user can read but may not own is the intended UX |
| IDOR | Backfill action reads `active_model` / `active_id` from context, scoping writes strictly to messages on that record |
| HTML in body | Read-only; never written back |
| Unbounded loops | `_message_fetch` honors `limit`; backfill iterates only the current record's messages (typical < 50) |
| XML ID leakage | All `<record>` ids prefixed `action_chatter_chrono_*`; refers only to public Odoo models |
| Tenant data | None — only `Blue Fox Inc.` metadata |
| `mail.mail` side-effects | Snapshotted before/after the backfill batch; a discrepancy is logged as a warning |

### Edge cases

| Case | Behavior |
|------|----------|
| `mail.message.date IS NULL` | `_cursor_domain` returns leaves that PostgreSQL evaluates safely; the message just doesn't participate in the cursor comparison |
| Two messages with identical `date` | Tie-break on `id`, identical to upstream's behavior |
| Discuss live channels | `date` and `create_date` are within milliseconds; no visible reorder |
| Backfill on a record with no email messages | Notification *Aucun courriel à analyser sur ce record* |
| Backfill where all messages already backdated | Notification *Rien à re-dater. N déjà backdaté(s)* |
| Backfill on a non-form context (no `active_id`) | Notification *Action invoquée hors du contexte d'un record* |
| Index already exists from a prior install | `CREATE INDEX IF NOT EXISTS` is a no-op |
| `newestPersistentAllMessages` JS compute | Left at `id desc` — this drives Discuss seen-tracking, not the chatter feed. Acceptable for BF where backdated messages never appear in Discuss |

## Installation

```bash
docker compose exec odoo odoo -d <database> -i bf_chatter_chronological --stop-after-init
```

After install, flush the asset cache so the JS patch ships:

```sql
DELETE FROM ir_attachment WHERE url LIKE '/web/assets/%';
```

then hard-refresh the browser (Ctrl/Cmd + Shift + R).

## Usage

The chatter on every model now displays messages in date order automatically — no user action needed.

To recover the original date of an imported email whose Date header was lost:

1. Open the affected record (task, ticket, contact, invoice…)
2. Click the cogwheel ⚙️ in the form toolbar
3. Choose **Réordonner ce chatter par date**
4. The notification toast reports how many messages were re-dated

Per-record backfill is idempotent and safe to run multiple times.

## License

LGPL-3

---

<sub>Authored and maintained by Blue Fox Inc. AI coding assistants were used as productivity tools during development.</sub>
