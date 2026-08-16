# Email Management for Odoo 18

A centralized email management module for Odoo 18 that provides a single, deduplicated inbox combining **direct IMAP ingestion** with **chatter projection** from `mail.message`. Includes a two-pane IMAP folder browser (Apple Mail / Thunderbird layout), UI re-routing of orphan IMAP emails into any Odoo record's chatter, bulk per-row target inference, RFC 2822 thread tracking, and an interactive OWL dashboard.

## Features

### Unified Inbox (1.5+)
- **Two ingestion sources, one row per Message-ID**:
  - **IMAP direct** — polls `INBOX` and `Sent` every 5 minutes via IMAP4_SSL, stores raw RFC 2822 for later re-use.
  - **Chatter / mail gateway** — projects `mail.message` rows that originated from Odoo chatter or were routed by the mail gateway.
- **Deduplication by Message-ID** — `UNIQUE(message_id_header, company_id, user_id)` constraint (per-owner since the 4.0 per-user pivot) plus active promotion: when a chatter `mail.message` arrives for an IMAP-orphan row, the row is upgraded in place (`source` switches from `imap` to `gateway`/`chatter`, `res_model`/`res_id` populated). Internal Odoo always wins.
- **Re-routing wizard** — single button on every IMAP-orphan row opens a wizard that posts the email to any model with `mail.thread` (project task, helpdesk ticket, contact, lead, calendar event, invoice, sale order, etc.). Preserves the original Message-ID and date so the RFC 2822 thread stays intact. Bulk action available from the list view.
- **Archives backfill wizard** — one-shot scan of any IMAP folder (e.g. `Archives/2025`) with optional date filters. Idempotent — re-running it never creates duplicates.

### Email Enrichment
- **Auto-categorization** — Client / Internal / Vendor / Notification / Marketing, computed from `res.partner` rank fields and sender pattern matching. Defensive against tenants without `sale_team`/`purchase` modules.
- **Rule engine (2.0+)** — `bf.email.rule` lets you encode "if sender matches X / subject contains Y / List-Unsubscribe present" → set category/priority/partner/handled. Rules fire on create after the auto-compute, with a Replay action for backfill.
- **Direction badges** — inbound (`←`) vs outbound (`→`), inferred from author membership and IMAP folder.
- **Four-state workflow (2.0+)** — `status` = New → Read → Replied (orthogonal to handled). `is_handled` = the Inbox-Zero "out of inbox" boolean. Archiving sets `is_handled=True` only — the read/replied status is preserved. The legacy `archived` value in `status` is kept as a tombstone for historical rows.
  - **new** — nobody has seen it yet.
  - **read** — the user (or chatter) opened it. Auto-flips on bf.email form open AND when the underlying mail.message is read in any chatter (via `mail.notification` override).
  - **replied** — an outbound message with matching `In-Reply-To` was sent.
  - **handled** — `is_handled=True`. Removed from the Inbox view, but `status` (and history) preserved.
- **Heuristic signals (2.0+)** — 8 stored booleans/floats per row, evidence-based. See §Research.
- **Response time** — automatically computed delta between an inbound row and the first outbound reply matching its Message-ID.

### RFC 2822 Thread Tracking
- **`thread_root_id`** — root Message-ID of the conversation, resolved from the `References` header (or `In-Reply-To` / parent chain). Indexed for fast grouping.
- **Conversation smart button** — opens the full thread filtered by `thread_root_id` directly from any row.
- **Group-by-thread** in the search view.

### Smart Actions
- **Reply / Forward — always available (2.0+)**. Single dispatcher with 4 branches:
  | direction | res_model | composer target               |
  |-----------|-----------|-------------------------------|
  | in        | yes       | source record (chatter)       |
  | in        | no        | own res.partner (orphan IMAP) |
  | out       | yes       | source record (chatter)       |
  | out       | no        | own res.partner (orphan IMAP) |
  Forward attaches original RFC 2822 attachments for orphan rows.
- **Snooze (2.0+)** — defer rows out of inbox until a chosen datetime. The IMAP mirror cron flips them back automatically.
- **Bilateral IMAP archive (2.0+, opt-in)** — set `writeback_archive=True` on the account and Archive in the UI also `UID COPY` + `EXPUNGE` from the IMAP INBOX.
- **Auto mark-as-read** — opening an email in the form automatically transitions the row. Reading the underlying `mail.message` in any chatter also flips status (via `mail.notification` override).
- **Auto mark-as-replied** — when an outbound row is created with `in_reply_to` matching an inbound row's Message-ID, the inbound is flipped to `replied` automatically (no manual click).
- **Bulk actions** — Mark read, Mark replied, Traiter, Remettre en boîte, Reporter, Re-router — all available as server actions on the list view.
- **Chatter buttons (6.0+)** — « Traité », « Reporter » and « Remettre en boîte » directly on each chatter message (hover/kebab actions, `mail.message/actions` registry, next to « Télécharger en .eml »). Resolve the *current user's* `bf.email` mirror by Message-ID; if none exists yet and the message is projectable, it is ingested on the spot then acted on — no round-trip to the Email app.
- **List reading pane (6.1+)** — optional Gmail/Outlook-style preview pane on the `bf.email` list view (`js_class="bf_email_preview_list"`). Position **right or bottom** (Outlook-style two-button control, persisted in localStorage), **drag-to-resize splitter** (25–75 %, size persisted per orientation), header + action buttons scroll away with the body (content-sized sandboxed iframe, `ResizeObserver` for late images). Row click loads the sanitized email (`body_html_display`), marks it read, and highlights the active row; **auto-advance** to the next email after Traité/Reporter; hotkeys `J`/`K`/`E`/`Escape`; clickable attachment badges (`get_preview_attachments`); Répondre / Transférer / Reporter / .eml / Ouvrir buttons. Pane off (default): the list is 100 % stock.
- **Per-recipient fan-out (6.0+)** — the chatter/gateway projection cron creates one `bf.email` row per involved internal user (author + notified recipients, `direction` relative to each owner), so every user's unified inbox carries the Odoo-internal traffic that concerns them. Service accounts are excluded via ICP `bf_email.route_exclude_user_ids`; messages with no internal user fall back to the cron user.
- **« Nouveau ▾ » — create a record from the email (5.3+; chatter import 5.5+)**. Header dropdown (OWL widget `bf_email_new_record_dropdown`) creates a **Tâche** (`project.task`), **Piste** (`crm.lead`), **Ticket** (`helpdesk.ticket`), **Dépense** (`hr.expense`), **Facture fournisseur** or **Facture client** (`account.move`) from the email. The record is created immediately and **the email itself is imported into its chatter** — rendered body + original attachments + the full reconstructed `.eml`, exactly like *Lier à un dossier* — instead of dumping the body into a `description` / `narration` text field. The source `bf.email` row is then filed under the new record and marked handled (`is_handled=True`). If a server-side create fails (e.g. a required field, or no employee for an expense), it falls back to the legacy pre-filled blank form so the button is never dead. The Piste / Ticket / Dépense items appear only when `crm` / `helpdesk_mgmt` / `hr_expense` are installed (`has_crm` / `has_helpdesk` / `has_expense`) — no hard manifest dependency. The attachments carried into the chatter are governed by `bf_email.import_attach_originals` / `bf_email.import_attach_eml` (both default on; the `.eml` already re-contains the originals, so storage-sensitive tenants can keep just one).

### Interactive Dashboard (OWL)
- Date range filters: 7d / 30d / 90d / year / all / custom — **all charts including daily volume now respect the selection** (preset "Tout" derives the range from the actual data).
- Inbox-Zero actionable cards: Boîte de réception active, En attente > 24h, IMAP orphelins à router, VIP en attente.
- "Tout traité ?" handled-rate badge — handled / total ratio for the selected period.
- KPI cards: received, sent, unread, average response time.
- Category breakdown, top contacts, daily volume chart.

### IMAP Folder Browser (3.5+, OWL client action)
A mail-client-style view of any IMAP folder, no permanent ingestion required.

- **Two-pane layout** — collapsible folder tree on the left (parent / child via `/` separator, e.g. `Archives > 2024 / 2025 / 2026`), message list and body preview stacked on the right.
- **Live folder metadata** — `LIST` discovery + `STATUS folder (MESSAGES UNSEEN)` per folder surfaces total / unread counts in the sidebar.
- **Unread display** — `\Seen` IMAP flag drives bold rows. Selecting a row auto-unbolds it locally; the server `\Seen` flag flips on next mail-client open.
- **Body rendering** — message bodies are rendered in a sandboxed `<iframe srcdoc>` so email-specific CSS (and large tables) don't bleed into Odoo's UI. Scripts inside emails are neutralised (no `allow-scripts`).
- **Per-row Traité button** — one-click ingest + IMAP archive + auto-jump to the next message.
- **Preview-pane toolbar** — Reply, Reply-All, Forward (open Odoo's mail composer wired to the ingested row), Traité (writeback to `Archives/{YYYY}` on the IMAP server), Router (open the Reroute wizard), Supprimer (IMAP `COPY uid Trash` + `EXPUNGE`).
- **Drag-and-drop** — drag a row onto any folder in the sidebar → `IMAP COPY` + `EXPUNGE`.
- **Infinite scroll** — `IntersectionObserver` on a sentinel `<div>` at the bottom of the list appends the next page (default 100) automatically.
- **Auto-jump** — after Traité / Supprimer / drag-and-drop, the preview moves to the next message; the cleared row is removed from the list in memory.
- **Keyboard shortcuts** — `J/K` or `↓/↑` navigate · `R` reply · `Shift+R` reply-all · `F` forward · `E` Traité · `Delete`/`Backspace` Trash · `Y` router · `S` or `/` focus search · `Escape` clear search. Implemented via `useHotkey` plus a native `/` listener (Odoo's hotkey service doesn't whitelist `/`).
- **In-page search** — filters the loaded page client-side against `subject` + `sender_name` + `from`.
- **Per-user settings** (gear icon, localStorage-backed):
  - Date format — relative (`aujourd'hui 14:35`, default) or absolute (`YYYY-MM-DD HH:mm`).
  - Sender display — name only, address only, or `Name <address>`.
  - Page size — 50 / 100 / 200 / 500.
  - Density — comfortable or compact (`table-sm`).
  - Bold unread rows — on/off.

The browser is read-only on the IMAP side except for opt-in actions (ingest, move, trash, archive). Connection lifecycle is one IMAP4_SSL session per RPC call — same pattern as `_cron_sync_imap` and `_cron_imap_mirror`.

### Bulk "Guess & import" (3.4+)
Server action bound to the `bf.email` list view (`Action → Deviner et importer`). For each selected IMAP-orphan row, calls `bf.email.reroute._suggest_target_reference` *per row* to pre-fill an independent target (high confidence when the contact has exactly one open task / ticket). Editable preview list with badge (high / aucune) — confirm routes N rows to N independent chatters in a single transaction, preserving Message-ID per row.

## Research grounding

The 8 heuristic signals are based on empirical email-overload research:

- **Dabbish & Kraut (2006)** — *Email Overload at Work: An Analysis of Factors Associated with Email Strain.* CSCW '06. Source for `is_question` (questions ~4× more likely to be replied to), `is_to_me` (direct-To ~3× higher response than CC), `is_action_request` (modal-verb cues correlate with perceived priority).
- **Whittaker & Sidner (1996)** — *Email Overload: Exploring Personal Information Management of Email.* CHI '96. Source for `is_short` (short emails answered fast = batch-able).
- **Whittaker (2011)** — *Personal Information Management.* Source for `is_likely_thread` (active threads benefit from batched handling).
- **Kooti et al. (2015)** — *Evolution of Conversations in the Age of Email Overload.* WWW '15. Source for `is_late_night` (off-hours skew low-urgency), `external_age_hours` (median reply <47 min, tail >24h is the real backlog), `expected_reply_minutes` (per-correspondent baseline beats global thresholds).
- **Grbovic et al. (2014)** — *How Many Folders Do You Really Need?* CIKM '14. Source for `is_bulk` (List-Unsubscribe is the strongest bulk signal in the Yahoo email taxonomy).

### Scheduled Drafts
- Cross-record list of every `mail.scheduled.message` the user can send.
- `scheduled_date` column with ascending default sort.
- Inline send-now and open-source actions.
- Editable form for subject, body, recipients, attachments.
- Inherits Odoo core's per-record post-access ACL.

### Views
- **List** — inbox-style; rows without a linked Odoo record are highlighted in warning color with an inline "Import to chatter" button.
- **Form** — full email detail with a **sanitized** rendered HTML body (`body_html_display`, parsed from raw RFC 2822 for IMAP-orphan rows), technical headers, smart buttons (Reply, Open chatter, Open record, Conversation thread).
- **Kanban** — grouped by status for visual workflow.
- **Search** — filters: To-reply / Stale > 7 days / Last 24-48h / Today / Week / Month / IMAP-only / With record / Without record / Archived. Group-by: direction, category, source, status, partner, model, thread, date.
- **Graph & Pivot** — volume analysis and cross-tabulation.

## Requirements

- Odoo 18 Community or Enterprise.
- `mail` module (included in Odoo) — provides `mail.message`, `mail.thread`, `mail.scheduled.message`.
- Additional Odoo module dependencies (manifest `depends`): `base`, `mail_composer_cc_bcc`, `calendar`, `project`, `account`, `bf_onboarding_base`.
- Python 3.10+ (uses standard library `imaplib`, `email.policy.default`, no extra pip deps).
- Optional: `mail_quoted_reply` for quoted-reply composer body.

## Installation

1. Copy the `bf_email_management` directory to your Odoo addons path.
2. Install via the Apps menu.
3. **Configure your IMAP account(s)** under *Courriels → Configuration → Mes comptes IMAP* (each internal user manages their own list — see Security below):
   - Host — your IMAP server hostname
   - Port — typically `993` (IMAPS)
   - Login — IMAP username (usually the email address)
   - Password — IMAP password or app password
   - Click **Test the connection**, save, then optionally **Sync now**.
4. The cron `Courriels : ingestion IMAP directe` runs every 5 minutes and silently skips users with no active account, so the module is safe to install before any user configures one.
5. To backfill historical emails from an archive folder, open *Courriels → Configuration → Rattrapage IMAP (Archives)*.

## Architecture Notes

### Watermarking
- **Chatter projection cron** advances a `create_date` watermark on `mail.message` (not the sender's `Date:` header) so back-dated imports — manual scripts, forwarded threads — never fall below the watermark.
- **IMAP cron** advances a per-folder UID watermark stored on each `bf.email.account` row (`last_uid_inbox`, `last_uid_sent`). IMAP UIDs are monotonic per folder.
- **Backfill wizard** does NOT touch the live UID watermarks, so re-running it on an archive folder is safe.
- **IMAP reconciliation pass (5.7+)** — `_cron_imap_reconcile` runs every 6 h, independent of the watermarks: it re-scans the last `bf_email.reconcile_days` days (default 30) of the live folders (`INBOX` + `Sent`) **read-only (EXAMINE)** and ingests any Message-ID with no `bf.email` row, closing the permanent-gap class left by forward-only watermarks. Idempotent — dedup by `message_id_header` + `user_id`. Call `_cron_imap_reconcile(days=60)` for a deeper one-shot recovery.

### Deduplication Order of Operations
- IMAP cron creates an `imap` row IF no `mail.message` with the same Message-ID exists. Otherwise it creates a `gateway`/`chatter` row directly linked to the existing `mail.message` (annotated with the IMAP UID for traceability).
- Chatter cron promotes any pre-existing `imap` orphan to `gateway`/`chatter` instead of creating a duplicate (UNIQUE constraint would reject otherwise).
- Migration `18.0.1.5.1` retroactively promoted historical orphans whose Message-IDs already existed in `mail.message`.

### Re-routing
- The wizard reads the stored RFC 2822 (kept in `raw_rfc822` Binary attachment), parses it, and posts to the target via `record.message_post(...)` preserving Message-ID, original date, author, and attachments.
- After posting, the `bf.email` row is promoted (linked to the new `mail.message`, `source` becomes `gateway`).

### IMAP Browser RPC surface (3.5+)
The OWL client action calls these `@api.model` methods on `bf.email`. Each opens a fresh IMAP4_SSL session, performs the operation, and logs out — no long-lived connections.

- `imap_browser_get_folders()` — `LIST` + per-folder `STATUS (MESSAGES UNSEEN)`. Returns `[{name, has_children, noselect, total_count, unread_count}]`.
- `imap_browser_get_messages(folder, offset, limit)` — `SELECT readonly` + `UID SEARCH ALL` + `fetch_headers_bulk` (one round-trip for the page). Returns `{folder, messages: [{uid, date, from, sender_name, subject, message_id, seen, already_in_bf_email}], total, offset, limit}`. Dedups against existing `bf.email` rows by Message-ID in a single SQL query.
- `imap_browser_get_body(folder, uid)` — full `UID FETCH RFC822` for one UID; returns `{subject, from, to, date, body_html, already_in_bf_email, bf_email_id, message_id}`.
- `imap_browser_ingest(folder, uid)` — fetch + `_ingest_rfc822` (dedup-safe). Returns `{bf_email_id}`.
- `imap_browser_ingest_and_reroute(folder, uid)` — ingest + return an `act_window` action opening the Reroute wizard with the new row pre-loaded.
- `imap_browser_reply(folder, uid)` / `imap_browser_reply_all` / `imap_browser_forward` — ingest if needed, then return the composer action via `action_reply` / `action_reply_all` / `action_forward` on the resulting `bf.email`.
- `imap_browser_mark_handled(folder, uid)` — ingest + `action_archive` (sets `is_handled=True` and triggers the bilateral writeback to `Archives/{YYYY}`).
- `imap_browser_move(folder, uid, dst_folder)` — `COPY uid dst_folder` + `STORE +FLAGS \Deleted` + `EXPUNGE`. Refuses empty / identical destination. Does NOT touch `bf.email`.
- `imap_browser_move_to_trash(folder, uid)` — `COPY uid Trash` + `EXPUNGE`. Refuses when source is already `Trash/*`.

## Security

- **Per-user model (4.0+)**: each internal user manages their own `bf.email.account` rows and only sees their own `bf.email` / `bf.email.rule` records. No admin bypass — even a superuser browsing through the UI honours the per-owner `ir.rule` (raw SQL queries in the dashboard also include an explicit `user_id` predicate).
- **Account credentials** (host / login / password) live on the `bf.email.account` row. The `ir.rule` `[('user_id', '=', user.id)]` makes them readable only by the owner.
- **Menu badge** for unread count.
- Re-routing wizard **and** the *Guess & import* bulk wizard validate `check_access_rights("write")` and `check_access_rule("write")` on the target record before posting into its chatter (the bulk path posts through a `sudo` proxy, so the user-level check is explicit — 5.7+).
- **IMAP command hardening (5.7+)** — `imaplib` does not validate its arguments, so every folder name, UID and header value interpolated into an IMAP command is escaped/validated through `bf_email_imap.imap_quote_mailbox` / `imap_uid_token` / `imap_reject_crlf`. CR/LF are rejected outright, so a crafted Message-ID, folder name or UID cannot inject a second command into the authenticated session.
- **No raw email HTML rendered with active content (5.7+)** — inbound HTML is stored raw (`body_html`, only NUL-stripped) but the form Body tab renders a sanitized projection (`body_html_display` = `html_sanitize(body_html)`) and the IMAP-browser preview field is `sanitize=True`. `body_html` is kept raw only for the reply/forward builders, which `html_sanitize` at their own use sites. The OWL browser preview additionally renders inside a scriptless sandboxed `<iframe>`.
- All SQL uses parameterized placeholders.
- `sudo()` calls are scoped to cross-model display-name lookups, partner resolution by email, and the IMAP cron loop (which fetches each active account in admin context, then runs `_ingest_rfc822` via `with_user(account.user_id)` so created rows inherit the account owner's `user_id`).
- IMAP body preview in the OWL browser renders inside `<iframe sandbox="allow-same-origin">` — no `allow-scripts`, so JS embedded in inbound mail is neutralised.
- Drag-and-drop and `Supprimer (Trash)` only touch IMAP state via parameterised `UID COPY` / `UID STORE` / `EXPUNGE`. Refuses permanent delete from `Trash/*` (user must go through the IMAP webmail).

## License

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-08-12, this version converts automatically to
  **LGPL-3.0-or-later**.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## Support

For support, please contact Les services de consultation Blue Fox, Inc. or open an issue in the repository.

---

Authored and maintained by Les services de consultation Blue Fox, Inc. AI coding assistants were used as productivity tools during development.
