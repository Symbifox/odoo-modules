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

### OWL Inbox (9.0+, client action)
The **Boîte de réception** menu is an OWL client action (`bf_email_inbox`) laid out exactly like the IMAP browser — folder rail on the left, message list on top, preview below — but fed by `bf.email` instead of an IMAP session.

- **Folders are states, not mailboxes** — Boîte de réception, Non lus, À répondre, Sans dossier, Reportés, Envoyés, Traités, plus a collapsible *Par catégorie* group (Client / Interne / Fournisseur / Notification / Marketing / Sans catégorie). Counts and unread badges come from one `search_count` per folder. The vocabulary deliberately mirrors `_mobile_filter_sql`, so phone, list view and client action agree on what "inbox" means. Category folders span **all** mail, handled included: scoping them to unhandled leaves them permanently empty on an inbox-zero mailbox, and what they are for is browsing the archive.
- **Composer (9.1+, target picker 9.2+)** — a new email attached to nothing, with an optional « Classer dans » field carrying the same `bf_chatter_target` picker as the rest of the instance. Pick a record and the composer is *retargeted before sending*, so the message is born on the right chatter with its followers and thread rather than being moved afterwards; the same hook covers scheduled sends, which read `model`/`res_ids` too. Leave it empty and nothing changes. ⚠️ `subject` and `body` are stored computes depending on `model`/`res_ids`: retargeting fires `_compute_body` (which blanks the body with no template) and `_compute_subject`, so both are read before and rewritten after, in a *separate* write — an explicit write removes the field from the recompute queue, which a write grouped with its own dependency does not guarantee. Odoo always posts on a record, so the created `bf.email` row is its own thread, the same trick `_composer_target` already uses for IMAP orphans; the row lands in *Sans dossier* and « Router… » files it later. The shell is born handled so an abandoned composer leaves nothing behind, and `inbox_close_compose` either adopts it (subject, recipients, Message-ID copied from the posted message) or deletes it.
- **Full preview toolbar** — Répondre, Répondre à tous, Transférer, Traité / Remettre en boîte, Reporter, Activité, Router… (with quick targets), Fil, Dossier, .eml, Ouvrir. Every one of them delegates to the existing record method through a server-side allow-list.
- **Server-side search** across the whole folder (subject, from, to, body preview) — the source is an indexed table, so there is no reason to filter only the loaded page the way the IMAP browser has to.
- **Drag a row onto a folder** — Traités marks it handled, Reportés opens the snooze wizard, Boîte de réception puts it back.
- **Shared preferences** with the IMAP browser (`bf_email_ui_common.js`): storage key, date format, sender rendering and preview scaffold all live in one place, so the two screens cannot drift apart.
- **Keyboard** — `J`/`K`/`↑`/`↓` navigate · `R` reply · `Shift+R` reply-all · `F` forward · `E` Traité · `Y` router · `H` reporter · `T` activité · `O` dossier · `C` composer · `S` or `/` search · `Escape` cancel.

The list view is still there under **Boîte de réception (liste)**: filters, group-by, pivot, export and server actions are things a client action does not provide, and there was no reason to lose them.

### Chatter handled indicator (9.0+)
Every chatter message carries a badge — **À traiter**, **Traité** or **Reporté** — reflecting the current user's `bf.email` mirror. `mail.message._to_store` joins `bfEmailState` in one query per rendered batch, and only on the `for_current_user` path: the state is strictly personal and must never ride along in a broadcast. The message actions follow the same state, so « Traité » disappears once the mail is out of the inbox and « Remettre en boîte » only shows where it means something.

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

### Mobile API (6.8+)
A plain REST/JSON contract under `/bf_email_management/mobile/v1/`, consumed by the **Odoo Inbox** Android app (the client is not part of this repository). Deliberately not `call_kw`: the app is a third party, and pinning it to the ORM wire format would make every model rename a breaking client change. The full endpoint list, payload shapes and error contract live in [MOBILE_API.md](MOBILE_API.md).

Authentication is bearer-token, and a token can only be obtained by completing a real Odoo web login: `GET /auth/start` runs under `auth="user"`, so password, SSO and TOTP all apply, and it redirects to the app's deep link with a single-use code (3 min TTL) that `POST /auth/exchange` swaps for the durable token. Every other route runs the request as the device's user, so the per-owner `ir.rule` on `bf.email` applies unchanged.

Offline compose is supported through a send ledger (`bf.email.mobile.send`) that de-duplicates replays, and push notifications go out over **UnifiedPush (ntfy)** — no Google dependency, and no push transport ships with a token: the publish credential lives in a system parameter, never in the source.

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
- Additional Odoo module dependencies (manifest `depends`): `base`, `mail_composer_cc_bcc`, `calendar`, `project`, `account`, `bf_onboarding_base`, `bf_chatter_target`.
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
- **Target selection is shared (8.2+).** The wizard, the *Guess & import* preview rows and the IMAP browser's quick-route all designate the destination through [`bf_chatter_target`](../bf_chatter_target): one search box over every chatter-bearing model, grouped results with an icon and a context line, and no model to pick first. A pasted Odoo URL, a bare id, a shorthand (`task:22299`) or a technical reference (`bf.email:17`) resolves in the same box and surfaces as an *Exact reference*. The module's own copy of the model list, of the resolver and the separate "quick link" field are gone, as are three `name_search` overrides on `project.task` / `res.partner` / `account.move` that only the old dropdown ever triggered.
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

### Inbox RPC surface (9.0+)
The inbox client action calls these `@api.model` methods on `bf.email`. They stay in the current user's environment — no `sudo` anywhere — so record rules remain the authority and a colleague's mailbox can never surface.

- `inbox_get_folders()` — `[{key, label, icon, parent, selectable, count, unread_count}]`. A parent (`selectable: False`) sums its children.
- `inbox_get_messages(folder, offset, limit, search)` — `{folder, messages, total, offset, limit}`, newest first, page size capped at 500. Unknown or non-selectable folder keys raise.
- `inbox_get_body(email_id)` — sanitized `body_html_display`, headers and attachments, and flips `new` → `read` for rows the user may actually write.
- `inbox_run_action(action, email_ids)` — named actions only, through `_INBOX_ACTIONS`: the method name comes from the browser, so the server decides what is nameable. Window actions returned here get an explicit `views` key, because `call_kw` — unlike `call_button` — never runs them through `clean_action()`, and without it the web client maps over `undefined` and the dialog silently never opens.
- `inbox_compose()` / `inbox_close_compose(shell_id)` — open the composer on a fresh unattached row, then adopt or delete that row when the dialog closes.
- `inbox_sync_now()` — same work as the list view's *Synchroniser maintenant*, but returns only the notification text. `action_sync_now` ends with `next: {tag: reload}`, which reloads the whole web client and would throw away the open preview and selection.

### IMAP write-back sweep (9.0+)
`_cron_imap_writeback_sweep(dry_run=False)`, hourly. `_cron_imap_reconcile` and `_cron_imap_mirror` both run server → Odoo; this one closes the other direction. It reads the Message-IDs actually sitting in INBOX, resolves each owner's row, and replays `_imap_writeback_archive` on the ones already marked handled — covering both a write-back that failed on a transient error (nothing ever retried it) and any code path that sets `is_handled` without going through `action_archive`. Rows born from the chatter carry no `account_id` and would be skipped by the write-back; since the sweep has just observed their physical copy in that account's INBOX, it binds them to it first. Bounded by INBOX size, idempotent, and `dry_run=True` turns it into a plain gap report.

## Security

- **Per-user model (4.0+)**: each internal user manages their own `bf.email.account` rows and only sees their own `bf.email` / `bf.email.rule` records. No admin bypass — even a superuser browsing through the UI honours the per-owner `ir.rule` (raw SQL queries in the dashboard also include an explicit `user_id` predicate).
- **Account credentials** (host / login / password) live on the `bf.email.account` row. The `ir.rule` `[('user_id', '=', user.id)]` makes them readable only by the owner.
- **Menu badge** for unread count.
- Re-routing wizard **and** the *Guess & import* bulk wizard validate `check_access_rights("write")` and `check_access_rule("write")` on the target record before posting into its chatter (the bulk path posts through a `sudo` proxy, so the user-level check is explicit — 5.7+).
- **IMAP command hardening (5.7+)** — `imaplib` does not validate its arguments, so every folder name, UID and header value interpolated into an IMAP command is escaped/validated through `bf_email_imap.imap_quote_mailbox` / `imap_uid_token` / `imap_reject_crlf`. CR/LF are rejected outright, so a crafted Message-ID, folder name or UID cannot inject a second command into the authenticated session.
- **No raw email HTML rendered with active content (5.7+)** — inbound HTML is stored raw (`body_html`, only NUL-stripped) but the form Body tab renders a sanitized projection (`body_html_display` = `html_sanitize(body_html)`) and the IMAP-browser preview field is `sanitize=True`. `body_html` is kept raw only for the reply/forward builders, which `html_sanitize` at their own use sites. The OWL browser preview additionally renders inside a scriptless sandboxed `<iframe>`.
- **Mobile bearer tokens (6.8+)** are `secrets.token_urlsafe(32)`, minted only by `/auth/start` behind a real web login, and never by a client. Device rows are owner-scoped with no client create right, and the token fields are kept off ordinary RPC reads. `group_email_admin` is deliberately **not** extended to the device model: a read-all rule there would hand every user's live mailbox token to any admin. Deactivating a user revokes their tokens, because tokens themselves do not expire. Recipient counts (50), bulk actions (100 rows) and sends per device per hour (100) are all capped, so a stolen token cannot turn the tenant's SMTP into a relay.
- **Push endpoints are SSRF-guarded** at registration *and* again at send time, redirects disabled — the server POSTs to that URL from a cron, so a private or loopback address is refused outright.
- **Mobile attachments are indexed by position** in their own message, never by `ir.attachment` id: the id space is global, and a device that could name an arbitrary one would be reading the whole filestore. Ownership is re-checked on every stream, and the response carries `no-store`.
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
