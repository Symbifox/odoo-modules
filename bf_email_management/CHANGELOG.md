# Changelog

All notable changes to `bf_email_management` are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This module follows Odoo's `MAJOR.MINOR.PATCH` convention prefixed with the Odoo series (`18.0.X.Y.Z`).

## [18.0.9.8.0] — 2026-08-21

### Added

- **`bf.email.imap_wake()` — the entry point for real-time ingestion.** A public
  method whose entire body is a `_trigger()` on the IMAP ingestion cron. An
  external IMAP IDLE watcher — one process holding an IDLE connection per active
  account — calls it over XML-RPC the moment the mail server announces an
  arrival; the cron worker, parked in `select()` on `LISTEN cron_trigger`, wakes
  within a second. Ingestion latency, and with it the mobile push that fires from
  `_sync_account`, drops from five minutes to seconds.

  It is deliberately a `_trigger()` rather than a direct call to
  `_cron_sync_imap`: ingestion stays inside the single cron worker, so a wake can
  never run alongside the scheduled pass — `_acquire_one_job` takes the row
  `FOR NO KEY UPDATE SKIP LOCKED`. The watcher is an accelerator, never a second
  ingestion path. If it dies, the cron keeps its own schedule and nothing is lost
  but latency.

  The method is safe for an ordinary internal user: the only thing it can do is
  make a cron that was going to run anyway run sooner. No administrator rights
  are needed, and there is nothing to configure in Odoo.

  Measured: **138 ms** from the wake to the start of ingestion when the scheduler
  thread is free. Any latency beyond that comes from `max_cron_threads = 1` plus
  the ordering in `_get_all_ready_jobs` (`failure_count, priority, id`) — a long
  job with a lower `id` systematically goes first when both are due. Setting
  `priority = 1` on the ingestion cron took a 10 s median down to 3.6 s.

  ⚠️ **A measurement trap worth knowing before concluding a server lacks IDLE.**
  Many servers advertise only a minimal capability set in the greeting and send
  the full list — `IDLE` included — in the `LOGIN` response. Python 3.14's
  `imaplib` does **not** refresh `conn.capabilities` from that response (3.13
  does), so reading capabilities after `login()` can hand back the greeting list
  and make a perfectly capable server look unsupported. Issue the command and
  look for `+ idling`; do not trust the list your client happens to hold.

## [18.0.9.7.0] — 2026-08-20

Consolidated entry covering 9.6.1 → 9.7.0, both released on 2026-08-20. All three
changes are in the calendar reminder toast served by `bf_calendar_reminder.js`.

### Fixed

- **Reminder buttons no longer break mid-word (9.6.1).** Odoo's notification toast
  is 400 px wide and lays its buttons out in a plain `d-flex` with no wrapping, so
  the seven buttons were squeezed below the width of their own labels, which the
  template's `text-break` class then split inside the word: *5 min* rendered on
  three lines, as *5 / m / in*. A new `static/src/scss/bf_calendar_reminder.scss`,
  scoped to the reminder toast alone through the notification's `className` prop so
  no other notification is touched, keeps each label whole and lets the row wrap:
  the four presets first, the remaining actions after. The non-breaking spaces the
  labels carried against the same defect were powerless — `overflow-wrap:
  break-word` breaks precisely inside a word, and making *5 min* a single unbreakable
  word only made it a longer thing to break — and are back to ordinary spaces.

### Changed

- **The dismiss button reads *Vu*, not *Ignorer*.** It ignores nothing: it records
  that the reminder was read and keeps it from firing again for that event. The
  label now says so.
- **Three levels of emphasis in the toast.** The dismiss button carries the brand
  colour (declared `primary`, the only per-button appearance lever the stock
  template exposes), *Ouvrir* stays a solid grey, and the five deferral buttons
  become outline buttons. The outline is expressed in transparent neutral grey
  rather than `.btn-outline-secondary`'s fixed tints, so the toast still reads in a
  dark theme. Since the template accepts no per-button class, the stylesheet
  recognises the deferrals as *the grey buttons that are not the last one*; if the
  button order changes, appearance shifts and nothing breaks.

### Added

- **A reminder settled in one window closes in the others.** The toast only ever
  existed in the browser that received it, so deferring or dismissing somewhere left
  the same reminder standing in every other tab, each asking for a decision already
  made. `bf_snooze` and `bf_dismiss` now broadcast `bf_calendar_reminder/close` on
  the attendee's own `bus.bus` channel — every session of theirs is already
  subscribed to it — and the client removes the toast for that event. The originating
  tab receives its own message; removing an already-removed toast does nothing. The
  broadcast leaves at transaction commit, hence after the state is written, so a
  window replaying `/calendar/notify` right afterwards reads a reminder that is
  already extinguished.

## [18.0.9.5.0] — 2026-08-18

Consolidated entry covering 9.3.0 → 9.5.0, all released on 2026-08-18.

### Added

- **Re-routing a mail that is already filed (9.3.0).** The wizard used to refuse outright — *re-routing mails already in a chatter is not supported* — which left the one filing mistake people actually make, noticing after the fact that a mail sits on the wrong record, with no remedy short of editing the database. `bf.email._move_chatter_message` now **moves** the existing `mail.message` instead of posting a second one: posting a copy would leave the original on the wrong record, which is the very thing being undone, and two chatters would carry the same Message-ID — which the ingestion cron's dedup reads as a duplicate to drop at random. Attachments follow, and the source record gets a note; a message vanishing without a trace is what costs an hour to understand six months later.
- **Pulling a message out of a record is a write on that record**, so the move now requires write access on both the old and the new one. Without that check, being able to read a mail would be enough to take it out of a colleague's record, and the trace would disappear from where someone else looks for it.
- **The button says what it does.** *Route…* while the mail is filed nowhere, *Re-route…* once it is, with the current record recalled at the top of the menu. One label for both read as the first.
- **Target suggestion follows the thread.** `_suggest_from_thread` proposes the record another mail of the same RFC 2822 thread is filed under — a far steadier signal than "this contact has exactly one open task", since the contact may have three tomorrow while the thread does not change records. Three guards: every selected row must share the **same** root (on a batch where one is an orphan, filing it after its neighbour is a guess drawn from a row that is not in the thread); the record already held is **excluded**, or a re-route would be offered the very place it is trying to leave; and a record the user cannot write is never proposed.
- **Add ▾ menu in the preview** — Task, Lead, Ticket, Expense, Vendor bill, Customer invoice, created *from* the mail and importing it into the new record's chatter. Same methods as the form view's *New ▾*, so the two cannot drift; entries for apps that are not installed are not offered, and a batch is refused with a readable message rather than a bare `ensure_one`.
- **Drafts folder** — the current user's `mail.scheduled.message` rows, soonest first (the reverse of mail: on sends still to come, what matters is the next one out). Preview, *Send now*, *Edit*, *Open record*, *Cancel*, and an overdue date shown in red. It is the only folder in the rail whose source is not `bf.email`: a scheduled send has neither Message-ID nor IMAP counterpart, and minting a `bf.email` row so it could appear here would make it a fake mail in every count — so the list swaps source per folder while keeping one output contract, and pagination, search and infinite scroll never learn which folder is open. Scope is *mine*, not *what I can see*: the model's own access already spans records the user can post on, which would surface a colleague's draft on a shared task.
- **Preview pane right, not only bottom** (`paneLayout`), with a draggable splitter and a size remembered **per layout** — the height you would give a mail body is not the width you would give it. Two toolbar buttons plus a preferences entry; applies to the IMAP browser too.
- **Column selector (9.4.0)** — a table button picks which columns the list shows: Date, Correspondent, Record, **Category**, **Snippet**, State (IMAP browser: Date, Sender, State). Kept with the other preferences and stored **per screen**, the two column sets genuinely differing. **Subject cannot be unticked** (shown ticked and disabled): without that floor, unticking everything builds an empty list with nothing left to say how to get out of it. *Category* reuses the labels the folder rail already computes rather than querying per row, so the column and the folder necessarily say the same word; *Snippet* surfaces `body_preview`, until now only a tooltip.
- **Select-all checkbox (9.5.0)**, three-state, at the head of the checkbox column. The partial state rides on the DOM `indeterminate` *property*, which no `t-att-` can set, hence the after-render hook; without it a partial selection would render as "nothing ticked", the opposite of what it is. It covers the **loaded** rows, not the folder — the list fills on scroll, and claiming to select three thousand mails when a hundred are in memory is a lie that comes due on the first click of *Handled*. The bulk bar spells it out as soon as the folder holds more than the current page.

### Changed

- **Compact density finally means one line.** It only added `table-sm`, which tightens padding without stopping a cell from wrapping — and wrapping is exactly what happens once the preview moves to the right and the list narrows, halving how many mails fit on screen. Compact now forces `table-layout: fixed` (without it the browser widens the column to fit and the ellipsis never triggers), `nowrap` and ellipsis on every cell, badges included. Column widths tighten with a right-hand pane as well, or the Subject column — the one you actually read — gets crushed.
- New shared stylesheet `static/src/scss/bf_email_ui.scss` for both client actions, and `paneStyles()` centralises their layout: while each carried its own `flex: 1 1 50%` inline, changing one did not change the other.

### Fixed

- `loadSettings` merged preferences flat except for `paneSize`. Any object-valued key added later would have been replaced wholesale by the stored value, so a new column would have been born **missing** for anyone who already had preferences — invisible in development, where local storage is empty. Deep merge now covers a declared list of keys.
- `_inbox_folder_domain("drafts")` **raises** instead of returning an empty domain: without that refusal, reaching *Drafts* by the wrong path would render the whole mailbox while claiming to render the drafts.

### Tests

- `tests/test_inbox_drafts_and_reroute.py` — 31 tests over the three new surfaces, **mutation-proven**: restoring the re-route refusal, dropping the "my drafts" scope, dropping the write check on the source record and lifting the single-row guard each fail exactly the matching tests and no others. Full module suite: 182 tests, 0 failures.

## [18.0.9.2.0] — 2026-08-18

Consolidated entry covering 9.0.0 → 9.2.0, all released on 2026-08-17/18.

### Added

- **The inbox is an OWL client action (9.0.0).** Same layout as the IMAP browser — folder rail on the left, message list on top, preview below — but fed by `bf.email` instead of an IMAP session, so the "folders" are states: Inbox, Unread, To reply, Unfiled, Snoozed, Sent, Handled, plus a *By category* group. The preview toolbar finally offers everything the IMAP browser had — reply-all, forward, route with quick targets, activity, thread, record — and the keyboard vocabulary is identical (`J`/`K`, `R`, `Shift+R`, `F`, `E`, `Y`, `H`, `T`, `O`, `C`, `S` or `/`). Dragging a row onto Handled, Snoozed or Inbox files it there. Search is served by the database over the whole folder, not filtered client-side over the loaded page. The list view stays available as *Inbox (list)*: filters, group-by, pivot and export are things a client action does not provide.
- **Shared UI foundation** (`bf_email_ui_common.js`): preferences, storage key, date format, sender rendering and preview scaffold live in one place, so the two screens cannot drift apart. Setting density on one sets it on both.
- **Handled indicator on every chatter message.** `mail.message._to_store` joins the current user's mirror state in one query per rendered batch, and only on the `for_current_user` path — the state is personal and must never ride along in a broadcast. The message actions follow it, so *Handled* disappears once the mail is out of the inbox.
- **Compose (9.1.0)** — a new email attached to nothing. Odoo always posts on a record, so the created row is its own thread, the same trick `_composer_target` uses for IMAP orphans. The shell is born handled, so an abandoned composer leaves nothing behind; on close it is either adopted (subject, recipients and Message-ID copied from the posted message) or deleted.
- **Target picker in the composer (9.2.0)** — an optional *File under* field carrying the shared `bf_chatter_target` picker. Pick a record and the composer is retargeted **before** sending, so the message is born on the right chatter with its followers and thread rather than being moved afterwards. The same hook covers scheduled sends, which read `model`/`res_ids` too.
- **Hourly write-back sweep `_cron_imap_writeback_sweep`.** `_cron_imap_reconcile` and `_cron_imap_mirror` both run server → Odoo; this closes the other direction. It reads the Message-IDs actually sitting in INBOX, resolves each owner's row, and replays the archive write-back on the ones already marked handled — covering a write-back that failed on a transient error, which nothing ever retried. Chatter-born rows carry no account and would be skipped, so the sweep binds them to the account whose INBOX it just observed them in. `dry_run=True` turns it into a plain gap report.

### Fixed

- **"Archive after import" left the mail in INBOX for good.** The re-route wizard wrote `active=False, status='archived'` by hand instead of calling `action_archive()`. Three silent consequences: `is_handled` stayed false so the mail never counted as handled; the row left *every* view, so it could not be found again to repair; and the IMAP write-back never fired, so the message stayed in the real mailbox. The wizard now goes through `action_archive()`, and the checkbox reads *Mark handled after import*.
- **The mirror cron could not see deactivated rows.** `search()` without `active_test=False` skipped them, freezing their IMAP state at the day they vanished and leaving snoozed ones asleep forever.
- **`imap_in_inbox` lied on chatter rows.** It defaulted to `True`, including on rows with no IMAP counterpart at all, so any gap count between the two sides started from a falsehood. It now defaults to `False`; only ingestion from an IMAP folder sets it. Views are unaffected — a chatter row enters the inbox through its `source`, not this flag.
- **The composed body survived neither retargeting nor the send.** `subject` and `body` are stored computes depending on `model`/`res_ids`: pointing the composer at another record fires `_compute_body`, which blanks the body when no template is set, and `_compute_subject`, which rewrites the subject from the record. Both are read before and rewritten after, in a *separate* write — an explicit write removes the field from the recompute queue, which a write grouped with its own dependency does not guarantee.

### Changed

- Category folders now span **all** mail, handled included, plus an *Uncategorised* folder. Scoping them to unhandled looked logical — a category sorts what is left to do — but on an inbox-zero mailbox they are then permanently empty and therefore useless. What they are for is browsing the archive.

## [18.0.8.2.0] — 2026-08-17

Consolidated entry. This release catches the public repository up from
18.0.6.7.2; the intermediate versions (6.8.0 → 8.1.0, developed and deployed
between 2026-08-15 and 2026-08-17) are grouped here by theme rather than
replayed one by one, because the tree they were built in is not itself
version-controlled.

### Added

- **Unified chatter-target picker (8.2.0).** The re-route wizard, the *Guess & import* preview rows and the IMAP browser's quick-route now designate their destination through the new `bf_chatter_target` module: one search box over every chatter-bearing model, results grouped by model with an icon and a context line, no model to pick first. A pasted Odoo URL, a bare id, a shorthand (`task:22299`), a technical reference (`bf.email:17`) or an invoice name resolves in the same box and surfaces as an *Exact reference*. This module's own copy of the model list, its copy of the resolver and the separate "quick link" field are gone.
- **Mobile REST/JSON API under `/bf_email_management/mobile/v1/` (6.8.0),** consumed by the Odoo Inbox Android app. Login is captured from the real web flow (`auth/start` is `auth="user"`, so password, SSO and TOTP all apply) and swapped for a bearer token; conversations are folded on `thread_root_id` rather than served row by row; remote content is blocked by default; attachments are indexed by position, never by `ir.attachment` id. Push runs over UnifiedPush (ntfy), with no Google dependency. Full contract in [MOBILE_API.md](MOBILE_API.md).
- **Outgoing attachments (6.9.0).** `POST /attachment/upload` (multipart, one file per call, 25 MB) parks a file and returns an id that `/reply` and `/compose` consume. `_mobile_claim_uploads` is a security boundary, not a lookup: without it `/reply` would accept any `ir.attachment` id.
- **Offline compose with a send ledger (7.0.0).** `bf.email.mobile.send` de-duplicates replays, so the app can write without a network and reconcile on reconnect.
- **Assertive test suite for the mobile API (7.0.1, 73 tests)** plus `tools/smoke_mobile_api.py`, which checks the contract's *shape* against a live instance and exits with a status code.
- **Address-book completion (`/contacts`) and rich-text bodies (8.0.0).** Plain text is escaped — what you type is text — while HTML mode is sanitised.
- **The instance advertises its branding to the app (7.3.0)**: company name, primary and dark colours, public URL, through `/ping` and `/config`.
- **Thread view can be turned off (8.1.0)** via `grouped` on `/threads`: each message becomes its own row, like an ordinary IMAP client.

### Security

- **A device token outlived the deactivation of its user (6.8.1).** Tokens never expire and `_resolve` only checked `device.active`, so archiving an account revoked the web session but said nothing about a bearer token issued months earlier. Deactivating the user is the one gesture everybody performs on departure, so it now closes this door too.
- **`/route` and `/compose` only checked half of the access rules (6.8.1)** — `check_access_rule` without `check_access_rights`.
- **A redirect could bypass the push SSRF guard (6.8.1).** `requests.post` follows redirects by default, so an endpoint verified as public could bounce to a private address. Redirects are now disabled and the host re-checked at send time.
- **Recipient, bulk and send caps (7.1.0).** 50 recipients across To + Cc, 100 rows per bulk action, 100 sends per device per hour on a sliding window. A bearer token lives on a phone and does not expire; the point is to bound the damage window, not to police normal use.
- **The attachment size cap fired too late (6.8.1)** — the controller materialised the bytes *before* comparing them to 25 MB.
- **Abandoned logins left a live device row forever (6.8.1).** `/auth/start` mints the device, token included, before the app has proved anything.

### Fixed

- **"Back to inbox" did not put anything back in the real inbox (7.2.0).** Archiving is bilateral — the message moves to `Archives/{YYYY}` on the IMAP server — but the reverse action only flipped the Odoo flag.
- **Opening a thread did not mark it read (6.8.1)**, so the notification badge disagreed with what the reader had plainly seen.
- **A sent attachment stayed parked under the upload marker (6.9.0)**, so the 24-hour garbage collector would have deleted a file out of an already-sent email.
- **Desktop snooze presets fired at the wrong hour (6.9.0).** `fields.Datetime.now().replace(hour=18)` is naive, therefore UTC.
- **Batched notifications arrived in reverse (6.8.1)** — the notification shade stacks by publication order.
- **"Compose" was broken for any user who is not a contacts manager (7.0.1).**

## [18.0.6.7.2] — 2026-07-24

### Fixed

- **The inbox raised an access error as soon as another user's email showed up in a list.** `web_read` ("mark as read on form open") flips every still-`new` row to `read`. But `web_search_read` calls `web_read` on the batch it returns (`odoo/addons/web/models/models.py:46`), so the override also runs on **lists**, not just on a single form. The two record rules shipped for `bf.email` are asymmetric — "visible to owner only" (`[('user_id','=',user.id)]`, full rights) and "admin sees all" (`[(1,'=',1)]`, **read-only**): a member of the "Email administrator" group can therefore read other people's rows but not write them. Any view that does not pin `user_id = uid` — the conversation thread (`action_open_conversation` filters on `thread_root_id` alone), a cleared filter, a global search — then returned a foreign row still marked `new`, and the implicit write failed **the entire request** with `AccessError`, not just the offending row. Marking is now restricted to rows the user can actually write (`_filtered_access("write")`), which also closes a privacy side effect: browsing someone else's mailbox no longer marks their email read on their behalf.

## [18.0.6.7.1] — 2026-07-24

### Fixed

- **"Sync now" failed with "Access to unauthorized or invalid companies" in a multi-company environment.** The three per-owner environments (`_cron_sync_emails`, and the two in `_sync_account`) were built with `with_user(target).with_company(target.company_id)`. But `with_company()` **prepends** to `allowed_company_ids` instead of replacing it: the caller's companies stayed in the context. Launched from the web UI with several companies active, the sync therefore switched to a target that does not belong to all of them, and `env.company` raised `AccessError` (`odoo/api.py`, `set(company_ids) - set(user_company_ids)`) — inside `_prepare_email_vals` (`"company_id": self.env.company.id`), **outside** the loop's `try/except`, so the whole action aborted without importing anything. The crons were never affected: they run with no `allowed_company_ids`, which is why the symptom only appeared on a manual trigger. The context is now **replaced** by the row owner's company alone.

## [18.0.6.7.0] — 2026-07-19

### Fixed

- **An email arriving through the gateway and marked "Handled" stayed in the IMAP inbox.** When the `bf.email` row already existed (chatter/gateway projection) at the moment the IMAP cron discovered the same Message-ID, `_ingest_rfc822` backfilled `imap_uid` / `imap_folder` / `imap_in_inbox` but **not** `account_id`. Yet the three mechanisms relying on that traceability all filter on `account_id`: `action_archive`, `_imap_writeback_archive` and `_cron_imap_mirror`. As a result, a row with source `gateway` or `chatter` was a candidate for neither bilateral archiving (the message stayed in INBOX indefinitely after clicking "Handled") nor mirroring (its `imap_in_inbox` never fell back to false). The backfill now sets `account_id`, and it also applies to rows that already had a UID but no account.

### Changed

- **The writeback checks the COPY status before deleting.** `_imap_writeback_archive` chained `COPY` then `STORE \Deleted` + `EXPUNGE` without reading the `COPY` status. `imaplib` only raises on `BAD`, never on `NO`: a refused `COPY` (missing target folder, quota, lock) was therefore followed by deleting the message, with no copy anywhere. A non-`OK` `COPY` now leaves the message in INBOX and logs a warning naming the target folder and the record.

### Known issue

- **The writeback trusts the stored `imap_uid` without verifying it.** When two `bf.email.account` rows target different mailboxes for the same user, a row may carry a UID recorded against the other mailbox. `UID COPY` then answers `OK` without copying anything (RFC 3501 requires silently ignoring absent UIDs), and the row is recorded as archived while the message has not moved. No email is lost, but the archiving is phantom in the database. Planned fix: check the UID against the Message-ID before trusting it, otherwise fall back to a `HEADER Message-ID` search.

## [18.0.6.5.0] — 2026-07-06

### Changed

- **Incoming emails with no notified user now fall back to the record's followers, not the cron user.** `_route_target_users` projected one `bf.email` row per internal user *notified* on the message; when none was (the typical case: a client reply logged on a record as a plain "Note" — the `mail.mt_note` subtype, which notifies nobody, and which is what Odoo does for replies to **invoices** `account.move`, unlike tasks, which arrive as "Discussion" and notify the followers), the message fell back to the user running the sync cron (the cron's uid), who thereby inherited every unattributable email. Now, absent a notified user, the row is assigned to the **internal followers of the underlying record** (`model`/`res_id`) — a reply to an invoice goes to the invoice's salesperson/followers, a reply to a task to its followers. Only genuine orphans (no linked record, or a record with no internal follower: bounces, third-party notifications) still fall back to the cron user, so nothing is lost. The fallback is bounded and defensive (uninstalled model, target not a `mail.thread`, deleted record): a failure to look up the followers can never break the projection cron. Retroactive: no — only new messages are routed this way.

## [18.0.6.4.1] — 2026-07-02

### Fixed

- **Replies to orphan emails no longer pollute the user's own contact record.** `_composer_target` fell back for orphans (no linked record) to the user's `res.partner`: every reply was posted in the chatter of their own record, and correspondents' replies came back there through threading (`References`). The fallback is now the `bf.email` row itself (it has inherited `mail.thread` since 4.0): the conversation stays attached to the email it continues. The historical threads accumulated on the record were reassigned to their records (or to their bf.email row) in the database.

## [18.0.6.4.0] — 2026-07-02

### Added

- **Reading pane: a batch of 6 refinements.** (1) **Gmail-style auto-advance**: "Handled" and "Snooze" load the next email in the list (the order is captured before the action, falling back to the previous one and then to the same position) instead of closing the pane. (2) **The active row is highlighted** in the list (`table-info`) — the renderer subscribes to the pane's state through the sub-env (`useSubEnv` plus a second `useState` on the renderer side for cross-component reactivity). (3) **Keyboard navigation**: `J`/`K` = next/previous, `E` = Handled, the same vocabulary as the IMAP browser (`useHotkey`, editable fields ignored; the arrow keys stay with the list's native navigation). (4) **Clickable attachments**: named badges with direct download (`/web/content`), through the new `get_preview_attachments` method (bf.email attachments for IMAP, `mail.message.attachment_ids` for chatter/gateway, without sudo). (5) **Forward** and **Download .eml** buttons in the action row. (6) **Escape closes the pane.**

## [18.0.6.3.0] — 2026-07-02

### Changed

- **Reading pane: more room, a resizable splitter and a scrolling header.** Wider defaults (right 44% → 50%, bottom 45% → 55%, width cap removed) and a **mouse resize handle** between the list and the pane (bounded 25–75%, size remembered per orientation in `localStorage` `bf_email.preview_size.right|bottom`; `pointer-events: none` on the iframe while dragging so the mouse is not lost). The header (subject, From/To/Cc, action buttons) **now scrolls with the body**: the iframe is auto-sized to its content (`contentDocument.scrollHeight` plus a `ResizeObserver` for late-loading images, possible thanks to `allow-same-origin`) and the outer container becomes the single scrolling context — the pane's full height goes to the message.

## [18.0.6.2.0] — 2026-07-02

### Changed

- **Reading pane: your choice of position, right or bottom (Outlook style).** The single button becomes a group of two in the control panel (columns = pane on the right, rotated columns = pane at the bottom); clicking the active position hides the pane. `bottom` mode: a vertical split (`flex-column`, pane at 45% height, `border-top`); `right` mode: unchanged (44% width). The `localStorage` preference is extended (`right`/`bottom`/`0`, with the old `1` migrated to `right`).

## [18.0.6.1.0] — 2026-07-02

### Added

- **An optional reading pane in the list view (Gmail/Outlook style).** A new `js_class="bf_email_preview_list"` on the `bf.email` list view: a button (columns icon) in the control panel enables a right-hand pane (~44%, hidden below lg). Enabled, clicking a row loads the email into the pane — headers (From/To/Cc/date/attachments/linked record), a **sanitised** body (`body_html_display`) rendered in a script-free `iframe sandbox` (the same pattern as the IMAP browser, links openable through `allow-popups`), and Reply / Handled / Return to inbox / Snooze / Open buttons — and marks the email "Read" (the row decoration is refreshed). Disabled, the standard list behaviour is intact. The preference is persisted in `localStorage` (`bf_email.preview_pane`). OWL template inheritance from `web.ListView` (primary mode, replacing the Renderer with `$0` — the literal must be the EXACT text content of the node, since `template_inheritance.js` matches `text()='$0'` strictly).

## [18.0.6.0.0] — 2026-07-01

### Added

- **"Handled" / "Snooze" / "Return to inbox" chatter buttons.** Three new actions on hover over each chatter message (the `mail.message/actions` registry, the same mechanism as "Download as .eml"): no more going back to the Emails app to take an email out of the inbox once the record is dealt with. The `bf.email` mirror is resolved by Message-ID plus the current `user_id` (`mail.message.action_bf_mark_handled` / `action_bf_snooze` / `action_bf_unhandle`); if no mirror exists yet and the message is projectable (an incoming email, or a comment that notified by email), it is ingested on the fly and then handled — the same contract as `imap_browser_mark_handled`. The buttons are visible on `email`-type messages and on non-note comments, for internal users only.
- **"Handled" closes the email's reminders.** `action_archive` now marks as done (with feedback) the open activities carried by the `bf.email` row itself — a handled email no longer nags. Activities on linked tasks/tickets are never touched.
- **Default rules for every new user.** The 4 factory sorting rules (noreply, List-Unsubscribe, client_rank, supplier_rank) only existed for the user who installed the module (XML records). They are now seeded automatically (through `bf.email.rule._seed_defaults_for_user`) when a user with no rules yet creates their first `bf.email.account`.

### Changed

- **Multi-user chatter/gateway projection (fan-out per recipient).** `_cron_sync_emails` assigned every Odoo email (chatter plus gateway) to the cron's user (`base.user_admin`) — a second user never saw the Odoo-internal emails, only their own IMAP. The cron now projects each message **once per internal user involved** (author plus notified recipients, through `_route_target_users`), each row created in its owner's environment (`with_user`, the same pattern as `_sync_account`): dedup, direction and rules apply per owner. It falls back to the cron user when no internal user is involved (nothing is lost). Service accounts are excluded through the `bf_email.route_exclude_user_ids` ICP (for example a service API user such as an automated process). The `UNIQUE (message_id_header, company_id, user_id)` constraint already supports the fan-out.
- **`_detect_direction` is now relative to the user.** "Outgoing" means *I am the author* (`env.user`), no longer "the author is some internal user". This is required by the fan-out: a colleague's email is incoming for me. No change for existing single-user data.
- **A coherent dashboard for the admin group.** The ORM KPIs (`_date_domain`) and every navigation action now carry an explicit `user_id = uid` bound, aligned with the SQL KPIs: a member of `group_email_admin` sees their own dashboard, not a global mix.
- **The contact record's "Emails" counter is bounded per user.** `res.partner.bf_email_count` (raw SQL, which bypasses record rules) now counts only the current user's rows — consistent with the drill-through.

### Security

- **The admin group no longer sees other people's IMAP accounts (passwords in clear).** The `bf_email_account_rule_admin_all` rule (global read) exposed every user's `password` field through RPC/export — `password="True"` in the view only masks the widget. The rule is removed: members of `group_email_admin` see every email and every rule, never other people's accounts.
- **"Initial import" restricted to the admin group.** The wizard read EVERY `mail.message` in the database in `sudo` (subjects plus record names beyond the access rules) and copied that metadata into rows owned by whoever ran it — available to any internal user. The menu is restricted to `group_email_admin`/`base.group_system`, plus an explicit `has_group` check in `action_run` (menu gating does not protect the RPC endpoint).
- **Consistent IMAP injection defence.** `imap_browser_get_folders` quoted the `STATUS` folder name by hand (`f'"{name}"'`) instead of using `imap_quote_mailbox` — now aligned with the rest of the module (defence in depth; the value comes from the server's `LIST`, not from the user).

### Notes

- A known and accepted single-user residue: `_cron_recompute_expected_reply` aggregates median response times per partner across all users (analytical, no visibility leak). The ntfy relay for calendar reminders remains a global endpoint (`bf_email.ntfy_reminder_url`).
- Historical chatter/gateway rows remain owned by the admin user; the fan-out applies to messages created after the deployment.

## [18.0.5.8.0] — 2026-06-25

*(entry reconstructed after the fact on 2026-07-01 — the version had been deployed without a changelog note)*

### Fixed

- **The menu badge, the "Inbox" action and the `filter_inbox` filter are scoped to `('user_id', '=', uid)`.** Before, a member of the "all emails" group saw EVERY user's inbox (and the badge counted everything). Deployed together with `bf_email_systray` v18.0.1.1.0 (per-user systray counter).

## [18.0.5.7.0] — 2026-06-17

### Security

- **Hardening against IMAP command injection (CRLF).** `imaplib` does not validate its arguments: a sender- or user-controlled value (Message-ID, folder name, UID) containing a `CR`/`LF` could inject a second command into the authenticated session. New centralised guards in `bf_email_imap` — `imap_quote_mailbox` (escapes `\`/`"`, rejects `CR`/`LF`), `imap_reject_crlf`, `imap_uid_token` (digits plus `, : *` only) — applied to: `select_folder` (covering every `SELECT`, including the browser/backfill wizards that bypassed the OWL validation), the `SEARCH HEADER Message-ID` and the target `COPY` in `_imap_writeback_archive`, and the `COPY`/`STORE` in `imap_browser_move` / `imap_browser_move_to_trash` (where the UID was previously a raw `str(uid)`).
- **Stored XSS — an incoming email's raw HTML is no longer rendered as-is.** The `body_html` field is deliberately unsanitised (preserving the source, with sanitisation at reply time). It was nonetheless displayed raw in the form and in the browser wizard's preview, executing an `<img onerror=…>` in the user's session. A new non-stored computed field `body_html_display` (= `html_sanitize(body_html)`) is rendered in the form; `bf.email.browser.preview_body_html` switches to `sanitize=True`. `body_html` stays raw for the reply/forward builders. (The OWL browser preview was already safe — an `iframe sandbox` without `allow-scripts`.)
- **Access control — the "guess the destination" wizard.** `bf.email.guess.route.action_confirm` posted the email into the target's chatter through a `sudo` proxy, without checking the user's rights on that target (the `Reference` field being freely editable). Added a per-target `check_access_rights('write')` plus `check_access_rule('write')` before posting, aligned with the re-routing wizard.

### Fixed

- **A permanent capture gap in the `Sent` folder.** The IMAP capture paths advance **one-way** watermarks: `_cron_sync_imap` by UID (`last_uid_inbox` / `last_uid_sent`). Any message skipped or transiently failing during a cycle is passed over **permanently** — never retried — because the watermark moves beyond it. Reconciling a live `Sent` folder against the `bf.email` rows: **492 of 493 messages already captured; 1 missing** — an email sent from a mail client (UID 7426), never imported into Odoo, which the IMAP pass should have turned into an orphan but which `last_uid_sent` had already moved past without creating a row. Recovered through the new reconciliation pass. *(Note: 3 other messages from a thread filed into a task appeared "missing" to an `active=True` query — they had in fact been captured and then archived by the filing; no bug.)*
  - **An IMAP reconciliation pass** (`_cron_imap_reconcile`, a cron every 6 h, `data/imap_reconcile_cron.xml`). Independent of the watermarks: it re-sweeps the last N days (the `bf_email.reconcile_days` ICP, default 30) of the live folders (`INBOX` + `Sent`) and ingests any `Message-ID` with no `bf.email` row for the owner. **Capture side only — IMAP read-only (EXAMINE), no COPY/EXPUNGE/write.** Idempotent (dedup by `message_id_header` plus `user_id`). Use `_cron_imap_reconcile(days=60)` or `folders=['Sent']` for a targeted one-off catch-up.

### Changed

- **The chatter projection cron's bound is hardened (latent).** `_cron_sync_emails` filtered by **strict** `create_date > last_sync`. Since `create_date` is not unique (a batch import inserts a whole thread at the same second), if the watermark lands exactly on that second, messages sharing that instant can be skipped with no way back. Changed to `create_date >= last_sync`; `_should_sync` already dedups by `(message_id, user_id)`, so no duplicate is created. *Preventive hardening — no incident was observed attributable to this bound, but the risk was real for emails sent through Odoo (SMTP) that are absent from the IMAP folders, which the reconciliation does not cover.*

## [18.0.5.6.0] — 2026-06-16

### Added

- **"New ▾ › Lead" (crm.lead).** A new menu entry creating a CRM lead/opportunity from the email, with the email imported into its chatter. A non-stored computed field `has_crm` (through `_compute_optional_apps`): the entry only appears when the `crm` module is installed (no new hard dependency). `crm.lead.type` defaults on its own (lead or opportunity depending on the "leads" feature).
- **Attachment de-duplication settings.** `bf_email.import_attach_originals` and `bf_email.import_attach_eml` (both on by default) allow disabling either the plain attachments or the `.eml` (which already contains them) when importing into a chatter — for storage-sensitive tenants.

### Changed

- **Shared implementation between reroute and "New ▾".** `bf.email.reroute._reroute_one` now delegates to `bf.email._import_into_chatter(target, force_file=True)`: a single implementation of "import an email into a chatter" (body plus attachments plus `.eml`), instead of two diverging copies. A welcome side effect: "Link to a record" now also attaches the `.eml`. The now-unnecessary `base64` / `bf_email_imap` imports were removed from the wizard.
- **"New ▾" marks the email "Handled".** After a record is created successfully, `is_handled=True` is set on the `bf.email` row (the "Handled" flag already used by the Inbox filter, independent of the read/replied status, reversible through "Return to inbox"): the email leaves the sorting queue once it has become a task/lead/invoice.

## [18.0.5.5.0] — 2026-06-16

### Changed

- **"New ▾" imports the email into the chatter, not into the description.** The five actions (`action_create_task` / `action_create_helpdesk_ticket` / `action_create_expense` / `action_create_vendor_bill` / `action_create_customer_invoice`) created the record through a blank prefilled form in which **the email body was poured into `description` / `narration`** — a free-text field that has no business receiving an email. The record is now created immediately and **the email is imported into its chatter** (rendered body plus the original attachments plus the full `.eml`, rebuilt where needed), then the `bf.email` row is filed under the new record (IMAP orphans promoted, the Message-ID preserved so future replies re-attach). This is exactly the artefact "Link to a record" already produces. The `description` / `narration` fields stay empty.
  - Since `helpdesk.ticket.description` is `required`, it receives a short pointer to the thread (the full email lives in the chatter).
  - New helpers `bf.email._import_into_chatter` (the same logic as `bf.email.reroute._reroute_one`), `_materialize_email_attachments` and `_spawn_from_email`.
  - **Safety net:** create plus import run inside a `savepoint`; any exception (a missing required field — for example no employee for an expense — or a failed import) falls back to the old blank `default_*` form, so the button is never stuck on a production record.

## [18.0.5.4.0] — 2026-05-25

### Fixed

- **Replying to orphan emails: a corrupted editor (cursor stuck inside the quote, signature swallowed).** The quoted body of IMAP rows with no chatter injected the original email's raw HTML (`body_html` is only NUL-stripped, never sanitised): full documents, `<style>` blocks, Outlook/`mso` residue, unclosed tags. Loaded as-is into the OWL editor, that HTML reorganised the DOM and swallowed the editable line and the signature placed above the quote. `_build_reply_quote_body` (the orphan branch) and `_build_forward_body` now run `tools.html_sanitize` over the body, as the chatter branch already does through `_prep_quoted_reply_body`.
- **A duplicated "Re:" on the subject.** The chatter's standard reply button (`mail_quoted_reply.reply_message`) prefixed `Re:` unconditionally → "Re: Re: …". On the `bf.email` side, `_open_composer` only checked for an exact `Re:` header and let "Re: Re:" and the French "Ré :" (with a space before the colon) through. A new `subject_utils.dedup_subject_prefix` helper reduces any stack of prefixes (`Re`/`Ré`/`Rép`/`Fwd`/`Fw`/`Tr`, with or without a space) to a single canonical prefix; applied in `_open_composer` **and** in a `mail.message.reply_message` override. The `mail_quoted_reply` dependency is now declared explicitly in the manifest (deterministic MRO order).

## [18.0.5.3.0] — 2026-05-20

### Added

- **A "New ▾" button on the email record.** A new OWL header widget (`bf_email_new_record_dropdown`) creating a record from the email: **Task** (`project.task`), **Ticket** (`helpdesk.ticket`), **Expense** (`hr.expense`), **Vendor bill** and **Customer invoice** (`account.move`, `in_invoice`/`out_invoice`). Each entry opens the **prefilled** creation form (subject → name/ref, contact → partner, body → description) through the `default_*` `context`; "create only" behaviour — the email is neither attached to the chatter nor marked "Handled" (use "Link to a record" for that). Backend: the `bf.email.action_create_task` / `action_create_helpdesk_ticket` / `action_create_expense` / `action_create_vendor_bill` / `action_create_customer_invoice` methods plus the `_open_create_form` helper.
- **Optional Helpdesk / Expenses detection.** Non-stored computed fields `has_helpdesk` / `has_expense` (`_compute_optional_apps`): the *Ticket* / *Expense* menu entries only appear when `helpdesk_mgmt` / `hr_expense` are installed. No new hard dependency in the manifest — the module stays portable.

## [18.0.5.2.0] — 2026-05-20

### Fixed

- **Replying to orphan emails: missing signature and "quote mode".** For IMAP rows with no associated chatter (`mail_message_id` empty), the reply body contained only a `<blockquote>` — no editable line above it, no signature. The body now reproduces the structure of `mail.message._prep_quoted_reply_body` (editable line plus the user's signature plus the quote), as chatter replies do. New helper `bf.email._compose_signature_block`.
- **Forwarding: an emptied body.** Forwards passed `is_quoted_reply=False`, but `mail_quoted_reply._compute_body` only injects `quote_body` when that flag is true — the forwarded body was therefore silently lost and the composer opened empty. `_open_composer` now forces `is_quoted_reply=True` for both reply **and** forward, and `_build_forward_body` includes the editable line plus the signature.

### Added

- **A read-only admin zone on emails.** A new `group_email_admin` group (the "Email management" category) with read-only `[(1, '=', 1)]` `ir.rule` records on `bf.email`, `bf.email.account` and `bf.email.rule`. ORed with the existing owner rules, they give an admin visibility over every email while normal users still see only their own; no write access on other people's rows. Group membership is NOT shipped as data — assign it manually, to humans only.
- **A dedicated admin menu plus a red banner.** A new "All emails — admin" action/menu (gated by `group_email_admin`, `bf_admin_zone` context); a permanent red banner in the form and `decoration-danger` colouring of rows belonging to another user, through the non-stored computed field `is_foreign_owner`.

## [18.0.5.0.0] — 2026-05-12

### Added

- **IMAP browser — quick-target reroute.** The **Route…** button in the preview pane and in the bulk action bar now exposes a dropdown with three frequent targets (Task, Ticket, Contact) plus *Other target…*. The chosen target prefills `target_reference` in the `bf.email.reroute` wizard: for `res.partner`, the email's partner is selected directly; for `project.task`/`helpdesk.ticket`, the existing suggestion is kept but bounded to the requested model. Backend: a new `bf.email.imap_browser_quick_reroute(folder, uids, target_model=None)` RPC accepting either a single UID or a list.
- **Multi-selection plus bulk reroute.** Every browser row shows a checkbox. When at least one is ticked, a blue action bar appears above the list: *N selected*, a **Route…** dropdown, **Handled**, **Clear selection**. The `bf.email.reroute` wizard posts one `mail.message` per email onto the common target. The selection is cleared when the folder changes or after a destructive action.
- **A "Snooze" button.** The `bf.email.snooze` wizard (which existed but was not wired into the OWL view) is now reachable from the preview pane and the `h` hotkey. Backend: `imap_browser_snooze(folder, uid)`.
- **An "Activity" button (create an activity from an email).** Opens `mail.activity` with `target="new"` and `default_res_model=bf.email`, `default_res_id`, `default_summary` (the truncated subject), `default_note` (From/Subject in HTML). Hotkey `t`. Backend: `imap_browser_create_activity(folder, uid)`.
- **A "State" column.** A new column in the message list showing, as Font Awesome icons with tooltips: `fa-check-circle` (already routed), `fa-moon-o` (snoozed, `snoozed_until > now`), `fa-reply` (status=`replied`). It replaces the old ✓ badge that was stacked in the action column.

### Changed

- **The "Route" button is always visible.** The preview pane's button is no longer hidden when the email has already been ingested into `bf.email`; it now allows re-routing to another record (still blocked by the wizard with an explicit `UserError` if the row is already attached to a chatter — unchanged wizard behaviour).
- **`imap_browser_get_messages` enriched.** Each message dict now includes `is_snoozed` and `is_replied` (computed from `bf.email.snoozed_until` and `status='replied'` respectively) to feed the State column without an extra round trip.
- **`bf.email.reroute._suggest_target_reference`** accepts a new `model_hint` kwarg (`project.task` / `helpdesk.ticket` / `res.partner`) bounding the suggestion to the requested model. Without a hint, behaviour is unchanged.
- **The `escape` hotkey** clears the multi-selection when it is non-empty, otherwise the search (the previous behaviour).

### Notes

- No schema migration — `bf.email.reroute.target_model_hint` is a Char on a `TransientModel`.

## [18.0.4.0.0] — 2026-05-10

### Breaking

- **Per-user pivot.** The module no longer manages a single shared IMAP mailbox. Each internal user now owns one or more `bf.email.account` rows and only sees their own `bf.email`, `bf.email.account`, and `bf.email.rule` records via a record-rule on `user_id`.
- **Security groups removed.** `group_email_user` and `group_email_manager` (and the module category) are unlinked by `migrations/18.0.4.0.0/post-migrate.py`. ACLs target `base.group_user` directly; the per-row `ir.rule` does the isolation. No admin bypass.
- **IMAP credentials moved.** The `ir.config_parameter` keys `bf_email.imap_host`, `imap_port`, `imap_user`, `imap_password`, `imap_archive_folder`, `imap_writeback_archive`, `imap_batch_size`, `imap_last_uid_inbox`, `imap_last_uid_sent`, `sync_batch_size`, and `auto_link_threshold_days` are migrated to per-account fields and then deleted. `bf_email.last_sync_date` is kept (chatter projection still uses it).
- **`_ingest_rfc822(raw, uid, folder, account)`** — signature changed: the last positional argument is now a `bf.email.account` row instead of the `configured_user` string. Same for `_sync_imap_folder(conn, folder, account)`.

### Added

- **`bf.email.account`** model — per-user IMAP credentials (host, port, login, password), per-folder UID watermarks (`last_uid_inbox`, `last_uid_sent`), per-account archive folder template, batch size, writeback toggle, auto-link threshold, plus a `state` (draft/connected/error) and `last_error` for diagnostics. Actions: **Test the connection** and **Sync now**.
- **Menu** `Emails → Configuration → My IMAP accounts` (replaces the legacy Settings panel) listing only the current user's accounts.
- **`bf.email.user_id` + `account_id`** fields. New SQL constraint `UNIQUE(message_id_header, company_id, user_id)` lets two users ingest the same Message-ID without collision.
- **`bf.email.rule.user_id`** — required Many2one on `res.users`. `_apply_rules()` only evaluates rules whose `user_id` matches the row owner; `action_replay_rules()` operates only on the current user's own rows.
- **Migration ICP override** — set `bf_email_management.legacy_owner_uid` on `ir.config_parameter` before upgrading to override the legacy-owner auto-resolution (defaults to the lowest active non-share user with `id > 1`).

### Changed

- **Crons refactored** — `_cron_sync_imap` and `_cron_imap_mirror` now iterate over `bf.email.account.search([('active', '=', True)])` and run each sync via `with_user(account.user_id)`, so new rows inherit `user_id` and `company_id` from the account owner.
- **Dashboard SQL** — every raw `cr.execute` in `bf_email_dashboard.py` now appends `AND be.user_id = %s` so direct-SQL aggregates respect per-user isolation (record rules don't fire on `cr.execute`).
- **`mail.notification` propagation** — read-state propagation no longer reads the legacy single IMAP user from ICP; it derives the recipient internal user from `res_partner_id` and only flips rows owned by that user.
- **`mail.message.action_download_eml`** — adds `('user_id', '=', self.env.uid)` to the bf.email mirror lookup to prevent cross-user raw-RFC2822 access via a shared chatter message.
- **Wizards** — `bf.email.browser` and `bf.email.imap.backfill` gain an `account_id` selector (default = current user's first active account; domain `[('user_id', '=', uid)]`). The IMAP browser RPC surface (`imap_browser_*`) accepts an optional `account_id` and validates ownership.

### Removed

- `res.config.settings` IMAP fields (`bf_email_imap_host`, `bf_email_imap_port`, `bf_email_imap_user`, `bf_email_imap_password`, `bf_email_imap_writeback_archive`, `bf_email_imap_archive_folder`, `bf_email_imap_batch_size`, `bf_email_auto_link_threshold_days`) and `action_bf_email_test_imap`. The functionality lives on `bf.email.account` now.
- Tenant-specific default rule `rule_internal_bluefox`. Users define their own internal-domain rules.

### Migration notes

The 18.0.4.0.0 migration:

1. Resolves the **legacy owner uid** (from `bf_email_management.legacy_owner_uid` ICP, or the lowest active non-share user id `> 1`, falling back to `SUPERUSER_ID`).
2. Adds `bf_email.user_id`, `bf_email.account_id`, and `bf_email_rule.user_id` columns via raw SQL in **pre-migrate** to satisfy the new `required=True` constraints during Odoo model setup.
3. Cleans up FK references that point to the legacy `group_email_user` / `group_email_manager` (`ir_model_access`, `res_groups_users_rel`, `rule_group_rel`, `res_groups_implied_rel`) so `unlink()` succeeds in post-migrate.
4. Drops the legacy `UNIQUE(message_id_header, company_id)` constraint so the new three-column constraint can take its place during the registry rebuild.
5. In **post-migrate**, creates a single `bf.email.account` row from the legacy ICP credentials (owned by the resolved owner), links the existing IMAP `bf.email` rows to it via `account_id`, deletes the legacy `bf_email.imap_*` ICP rows, and unlinks the legacy groups + the module category record.

## [18.0.3.8.0] — 2026-05-10

### Added
- **IMAP browser preferences** — a gear button next to the search bar opens a panel with 5 settings persisted in `localStorage` (key `bf_email_browser_settings_v1`, versioned schema):
  - **Date format**: relative ("today 14:35", the default) versus absolute ("2026-05-10 14:35").
  - **Sender display**: name only (default), address only, or "Name &lt;address&gt;".
  - **Messages per page**: 50 / 100 (default) / 200 / 500 — reloads the current folder on change.
  - **Display density**: comfortable (default) or compact (`table-sm`).
  - **Bold unread messages**: on (default) / off.

### Notes
- The panel is purely client-side: no server round trip, no Odoo table. Cross-browser means unsynchronised (deliberate — each workstation sets its own comfort). Per-user sync through `res.users` will come if the need appears.

## [18.0.3.7.0] — 2026-05-10

### Added
- **IMAP browser — UX round 2**: 10 Apple Mail / Thunderbird style improvements, in a single commit.
  - **A folder tree** in the sidebar: `Archives` expands into `2024 / 2025 / 2026` (parsed on `/`). A `▸ / ▾` toggle, with parents auto-expanded on first load.
  - **Unread plus total counters** per folder: `imap_browser_get_folders` calls `STATUS folder (MESSAGES UNSEEN)` after the `LIST`. A blue badge when unread > 0, muted grey otherwise.
  - **Bold rows when unread**: `fetch_headers_bulk` now retrieves the IMAP `FLAGS` alongside the headers; the absence of `\Seen` gives `fw-bold`.
  - **A parsed sender name**: `email.utils.parseaddr` server-side; the *Sender* column shows "Symbifox" instead of "Symbifox &lt;notifications@github.com&gt;". The full address is in the tooltip.
  - **Apple Mail style relative dates**: `today 14:35` / `yesterday 09:12` / `Mon 14:35` / `5 May` / `2024-12-05` depending on age.
  - **Keyboard shortcuts** through `useHotkey` (Odoo core): `J/K` or `↓/↑` to navigate · `R` reply · `Shift+R` reply all · `F` forward · `E` Handled · `Del`/`Backspace` Trash · `Y` route · `/` focus search · `Esc` clear search.
  - **Search within the loaded page**: an `<input type="search">` above the list, filtering `subject` + `sender_name` + `from` client-side in real time.
  - **A per-row "Handled" button**: a `✓` icon at the right of each row. No need to click the row first — a single click ingests, archives and jumps to the next row.
  - **Automatic jump to the next message** after Handled / Delete / drag-and-drop. No more empty preview pane in the middle of triage.
  - **Infinite scrolling**: the Previous / Next buttons are gone, replaced by an `IntersectionObserver` loading the next page when you scroll down (rootMargin 200 px). Pages already loaded stay visible.
  - **Drag-and-drop between folders**: dragging a row onto a folder in the sidebar calls `imap_browser_move` server-side (COPY + EXPUNGE).

### Added (server)
- `bf_email_imap.fetch_headers_bulk` now returns `{uid: (msg, seen)}` instead of `{uid: msg}`. Two call sites updated (`bf.email.imap_browser_get_messages` and `bf.email.browser.action_load_page`).
- `bf.email.imap_browser_reply_all(folder, uid)` for the Shift+R shortcut, mirroring `imap_browser_reply`.
- `bf.email.imap_browser_move(folder, uid, dst_folder)` for drag-and-drop. Refuses an empty target, or one equal to the source.

## [18.0.3.6.0] — 2026-05-10

### Changed
- **IMAP browser: iframe preview plus full actions** — the email body is now rendered inside an `<iframe sandbox="allow-same-origin" srcdoc="…">` with a small HTML container (Lexend / system fallback, 12 px margins, `img { max-width: 100% }`, `pre { white-space: pre-wrap }`, blue-grey quotes). No more CSS leaking between the email and the Odoo interface, and no more horizontal overflow on emails with 800 px wide tables.
- **An action bar under the subject**: *Reply* (auto-ingestion plus a composer pointed at the bf.email row), *Forward* (same, in forward mode), *Handled* (auto-ingestion plus `action_archive`, which COPY+EXPUNGEs to `Archives/{YYYY}`), *Route* (only when not yet ingested), and *Delete* on the right (moves to `Trash` on the IMAP server through COPY+EXPUNGE, without touching bf.email). After *Handled* or *Delete*, the message is removed from the in-memory list.

### Added
- 4 new RPC methods on `bf.email` consumed by the OWL client:
  - `imap_browser_reply(folder, uid)` — conditional ingestion plus `action_reply()`
  - `imap_browser_forward(folder, uid)` — conditional ingestion plus `action_forward()`
  - `imap_browser_mark_handled(folder, uid)` — conditional ingestion plus `action_archive()` (bilateral writeback)
  - `imap_browser_move_to_trash(folder, uid)` — IMAP `COPY uid Trash` plus `EXPUNGE` in the source folder

### Notes
- *Delete* explicitly refuses when the source folder is already `Trash/*` — no permanent deletion from this browser, use the IMAP webmail.
- The iframe has `allow-same-origin` but no `allow-scripts`: scripts inside emails are neutralised.

## [18.0.3.5.0] — 2026-05-10

### Changed
- **The IMAP browser rebuilt as two panes, Apple Mail / Thunderbird style** — the action moves from a `bf.email.browser` form view to an OWL client action (`bf_email_browser`). Layout: a left sidebar (240 px) with the IMAP folder list, and a right panel split vertically (50/50) between the message list at the top and the body at the bottom. Clicking a folder loads the first page (newest first, 100 messages). Clicking a message loads the body through FETCH RFC822 and shows it in the bottom panel with *Ingest* / *Ingest and route* buttons. All IMAP I/O goes through 5 new `imap_browser_*` RPC methods on `bf.email`; the `bf.email.browser` TransientModel remains as a diagnostic action not tied to a menu.
- Real pagination (Previous / Next 100 messages) with a "1–100 / 7219" counter in the list header.

### Notes
- The `action_bf_email_browser` action is now an `ir.actions.client` (tag `bf_email_browser`) — the menu points at the same place, but opens the OWL view instead of the transient form.
- Version 18.0.3.4.0 added the TransientModel plus its form; 18.0.3.5.0 keeps that as a fallback and makes OWL the default path.

## [18.0.3.4.0] — 2026-05-10

### Added
- **IMAP browser** — a `bf.email.browser` wizard (menu: Emails → IMAP browser) opening any IMAP folder (Archives/2024, Trash, Junk, Drafts, Templates, Snoozed, …) read-only, with no automatic ingestion. Shows up to 500 messages per page (newest first), with a full-screen preview (subject / sender / HTML body), an "Already ingested" indicator per row, and three per-row actions: *Preview*, *Ingest* (creates the `bf.email` row), *Ingest and route* (opens the prefilled Reroute wizard). Reuses the existing `bf_email_imap` helpers (`open_connection`, `select_folder`, `search_uids_in_range`, `fetch_rfc822`, `parse_rfc822`, `extract_body`) and `bf.email._ingest_rfc822` for ingestion.
- **`bf_email_imap.fetch_headers_bulk(conn, uids)`** — a new helper doing a single `FETCH (BODY.PEEK[HEADER.FIELDS (DATE FROM SUBJECT MESSAGE-ID)])` over N UIDs and returning `{uid: email.message.EmailMessage}`. Avoids N IMAP round trips to populate the browser's table.
- **"Guess and import"** — a bulk server action on the `bf.email` list (Action → Guess and import). For each selected IMAP-orphan row, it runs `bf.email.reroute._suggest_target_reference` *per row* (rather than globally) so that a distinct target is prefilled per email when the contact has exactly one open task or ticket. Shows an editable list with a confidence badge (high / none) that the user can correct before confirming. A single confirmation routes N rows to N independent targets through `bf.email.reroute._reroute_one`, propagating the `mark_replied` / `archive_after` flags.

### Notes
- The browser never writes to the IMAP server (every selection is `readonly=True`). No risk of accidentally ingesting Trash / Junk: it takes an explicit click per message.
- "Guess and import" and the existing Reroute wizard coexist: the classic Reroute stays useful when every selected row goes to the *same* target. The new wizard is for independent N→N.
- No migration needed — both new models are `TransientModel`s. The tables are created automatically on install/upgrade.

## [18.0.3.3.0] — 2026-05-10

### Fixed
- **Rule-driven auto-handle never archived on IMAP** — `_apply_rules` wrote `is_handled=True` directly via `rec.write(vals)` and never invoked `_imap_writeback_archive`. Rules like *"List-Unsubscribe → Marketing + Traité"* and *"Expéditeurs noreply → Notification + Traité"* therefore left every matching message in the IMAP INBOX while marking it Traité in Odoo. The 18.0.2.4.0 backfill caught the chatter/gateway race cohort but not this one — they were two distinct root causes. `_apply_rules` now collects records that transitioned to handled and calls `_imap_writeback_archive` on the batch (gated on the same ICP `bf_email.imap_writeback_archive`, exception caught + warned).

### Migration
- `migrations/18.0.3.3.0/post-migrate.py` — same 180-day handled-but-still-in-inbox replay as 18.0.2.4.0, in 50-row IMAP chunks. Catches up rows accumulated between the 2.4.0 deployment and the 3.3.0 fix.

## [18.0.3.2.0] — 2026-05-10

### Changed
- **Reuse `mail_composer_cc_bcc` for Cc / Bcc** — 18.0.3.0.0 introduced its own `bf_to_partner_ids` / `bf_cc_partner_ids` / `bf_bcc_partner_ids` fields with a parallel composer view, but BF prod already had `mail_composer_cc_bcc` (Camptocamp / OCA-style) installed since 2026-03 with `partner_cc_ids` / `partner_bcc_ids` always-visible on the composer. Result: when the BF Reply-All flag was on, the user saw two Cc fields and two Bcc fields stacked. The split fields and view override are now removed; the BF Reply-All dispatcher feeds the existing `mail_composer_cc_bcc` plumbing via `default_partner_cc_ids` in context. To make the context defaults survive the inherited `_compute_partner_cc_bcc_ids` recompute (which otherwise resets to the company default on every fresh wizard), this module now overrides that compute and honors the context defaults when present.
- **Manifest now hard-depends on `mail_composer_cc_bcc`** — previously bf_email_management worked standalone; now we rely on its fields, so it's listed in `depends`.

### Added
- **Settings page « Inbox unifiée »** — Settings → Inbox unifiée surfaces the IMAP credentials (host / port / user / password), the bilateral archive toggle + folder template, the IMAP batch size and the auto-link threshold. All four params previously required Technical → Parameters → System Parameters editing. A "Tester la connexion" button opens an IMAP4_SSL session with the saved credentials and reports the INBOX count + the list of folders detected (capped at 25). Menu shortcut: Courriels → Configuration → Paramètres (compte IMAP).

### Migration
- `migrations/18.0.3.2.0/post-migrate.py` drops the three legacy m2m tables (`bf_compose_to_partner_rel`, `bf_compose_cc_partner_rel`, `bf_compose_bcc_partner_rel`), the dangling `ir.model.fields` rows for the four dropped fields, and the orphan view `bf_email_management.bf_email_compose_message_wizard_form`. No persistent data — these were all transient wizard fields.

## [18.0.3.1.0] — 2026-05-09

### Changed
- **Calendar reminder popup re-shows on page load** — the OWL `calendarNotification` service replacement now calls `getNextCalendarNotif()` on `start()`, in addition to subscribing to the `calendar.alarm` bus channel. Previously, an alarm that fired while the tab was closed would silently disappear: bus.bus only re-emits when `_notify_next_alarm` is called server-side, never on client connect, so a refresh after a missed alarm dropped it. Now, on every page load, the service polls `/calendar/notify` to surface any pending alarm whose `notify_at > calendar_last_notif_ack`.
- **Snooze button labels shortened + non-breaking spaces** — Odoo's notification toast renders 7+ buttons in a narrow strip and was wrapping French labels mid-word (e.g. `"Demain 8 h"` → `"De\nma\nin\n8\nh"`, `"Détails"` → `"D\nét\nail\ns"`). New labels: `5 min` / `15 min` / `1 h` (with U+00A0 NBSP between the number and unit) / `Demain` / `Autre…` / `Ignorer` / `Ouvrir`. Behavior unchanged — only display strings.

## [18.0.3.0.0] — 2026-05-07

### Added
- **An enriched To / Cc / Bcc composer** — the `mail.compose.message` override adds three distinct Many2many fields (`bf_to_partner_ids`, `bf_cc_partner_ids`, `bf_bcc_partner_ids`) activated by the `bf_email_split_recipients` flag. When the composer is opened from the unified inbox (`bf.email.action_reply`, `action_reply_all`, `action_forward`), those three fields replace the monolithic `partner_ids` list. The three lists are merged into `partner_ids` on send so the standard notification flow is respected, and `email_to`/`email_cc` are injected into `_prepare_mail_values_*` so the outgoing To and Cc headers reflect the separation. Bcc recipients receive the email through `partner_ids` but appear in neither To nor Cc (Gmail style). On any composer opened elsewhere in Odoo, the flag stays `False` and standard behaviour is intact.
- **A "Reply all" button** in the `bf.email` form header for incoming emails. It prefills To with the original sender and Cc with the thread's other recipients (To+Cc), excluding the current user and internal aliases (`mail.bounce.alias`, `mail.catchall.alias`, `mail.default.from`, `bf_email.imap_user`).
- **Unified search in the re-routing wizard** — `bf.email.reroute` now passes the `bf_email_reroute_search=True` context to the `target_reference` field. The `name_search` overrides on `project.task`, `account.move` and `res.partner` detect that flag in order to:
  - accept a bare integer (e.g. `22299`) and resolve to `id = 22299`,
  - accept the invoice/entry format (`INV/2026/00017`) as an exact match on `name`,
  - return enriched labels `#{id} — {display_name}` (and `… <email@…>` for contacts) so the dropdown shows the full name.
- **A "Quick link" field** in the wizard. Accepts an Odoo URL (`https://.../all-tasks/22299`, `/odoo/project/N/22299`), a prefix (`task:22299`, `ticket:42`, `partner:1234`, `invoice:NNN`), a bare integer, or an invoice name. Resolves `target_reference` automatically through onchange.
- **`target_reference` prefill** — when every selected row shares a single `partner_id`, the wizard looks for a single open task (`state in [01_in_progress, 02_changes_requested]`) or a single open helpdesk ticket for that partner, and pre-suggests it.
- **A "Wake-up" column** (optional, hidden by default) in the `bf.email` list — shows `snoozed_until` with the `remaining_days` widget, to visualise when snoozed emails come back.
- **The `_cron_auto_link_orphans` cron** (disabled by default, 6 h interval) — auto-links orphan IMAP rows (`source='imap'`, `res_model=False`) to the contact's single open task or ticket. Conservative: a single exact match, a customer or vendor partner, a window configurable through `bf_email.auto_link_threshold_days` (default 14 days). Nothing is posted to the chatter — it is a soft link, and re-routing stays at the user's discretion.

### Notes
- No migration needed: the new Many2many fields on `mail.compose.message` are transient wizard fields (never persisted). The new parameters are added with `noupdate="1"`.
- The auto-link cron stays `active=False` for a cautious deployment. Enable it manually through Settings → Technical → Scheduled Actions once validated in review.
- The module does not depend on Helpdesk (`helpdesk_mgmt`): the helpdesk branch in the prefill and the auto-link is guarded by `'helpdesk.ticket' in self.env`.

## [18.0.2.4.0] — 2026-05-06

### Fixed
- **IMAP writeback never archived gateway/chatter rows server-side** — when the chatter cron created the `bf.email` row before the IMAP cron saw the UID (a 5-minute race that played out for ~24% of inbound gateway rows), `_ingest_rfc822` skipped backfilling `imap_uid` because it only touched rows whose `source` was already `imap`. Without a UID, `_imap_writeback_archive` filtered those rows out and silently no-op'd. Two changes:
  - `_ingest_rfc822` now backfills `imap_uid`/`imap_folder`/`imap_in_inbox` on any existing row that lacks them, regardless of source.
  - `_imap_writeback_archive` falls back to `IMAP SEARCH HEADER Message-ID` against INBOX when the UID is missing or stale, so gateway/chatter rows still get archived even if they never picked up a UID via the cron path.

### Migration
- `migrations/18.0.2.4.0/post-migrate.py` — replays the IMAP writeback for `is_handled=True AND imap_in_inbox=True` rows from the last 180 days, in 50-row IMAP chunks. Catches up the historical backlog (~2.6k handled rows that never moved server-side).

## [18.0.2.1.0] — 2026-05-05

### Added
- **.eml download** — a "Download .eml" button in the `bf.email` form header, and a "Download as .eml" entry in every chatter message's kebab menu (visible to internal users, on `email`-type messages). For rows with `raw_rfc822` (direct IMAP ingestion), the original RFC 2822 bytes are served as-is — `Received:`, `DKIM-Signature:` and so on are preserved. For chatter/gateway rows, the `.eml` is rebuilt from `mail.message` (From/To/Cc/Subject/Date/Message-ID/In-Reply-To, a multipart text+HTML body, attachments).
- A new `mail.message` inheritance with an `action_download_eml` method delegating to any `bf.email` mirror (looked up by `Message-ID`) before rebuilding.
- Helpers on `bf.email`: `_build_eml_bytes`, `_build_eml_from_mail_message` (class), `_build_eml_from_self`, `_eml_filename`, `_eml_slug`.
- The OWL asset `static/src/js/bf_email_chatter_action.js` registered in the `mail.message/actions` registry (`sequence: 80`).

## [18.0.1.5.1] — 2026-04-28

### Fixed
- **Empty body on IMAP-orphan rows** — `body_html` was a related field on `mail.message.body`, which returned empty for IMAP-direct rows that have no linked `mail.message`. Converted to a stored compute that parses `raw_rfc822` for `source='imap'` rows and reads `mail.message.body` for chatter/gateway rows. Plain-text bodies are wrapped in `<pre>` for chatter-style rendering.
- **Internal Odoo wins on dedup** — the IMAP cron previously created orphan rows even when a `mail.message` with the same Message-ID already existed (because the chatter projection had run earlier and the IMAP UID was new). It now checks `mail.message` proactively and creates the row already linked to the chatter (annotated with IMAP UID for traceability) instead of as an orphan.
- **NUL byte stripping** — PostgreSQL `TEXT` columns reject `0x00` bytes; some clients embed them via inline images or quoted-printable artifacts. Bodies are now scrubbed before storage to prevent `A string literal cannot contain NUL` errors during compute persistence.

### Migration
- `migrations/18.0.1.5.1/post-migrate.py`:
  1. Retroactively promotes IMAP orphans whose Message-ID already exists in `mail.message` (link `mail_message_id`, copy `res_model`/`res_id`, switch `source` to `gateway`/`chatter`).
  2. Backfills `body_html` from `mail.message.body` for chatter/gateway rows (fast SQL path).
  3. Backfills `record_name` for newly-promoted rows.
  4. Recomputes `body_html` for remaining IMAP orphans by parsing `raw_rfc822`.

## [18.0.1.5.0] — 2026-04-28

### Added
- **Direct IMAP ingestion** — new cron `_cron_sync_imap` (5-minute interval) connects via IMAP4_SSL to a configured mailbox, polls `INBOX` and `Sent`, and creates `bf.email` rows with `source='imap'`. Per-folder UID watermarks (`bf_email.imap_last_uid_inbox`, `imap_last_uid_sent`).
- **Re-routing wizard** (`bf.email.reroute`) — single-record button on every IMAP-orphan row, plus list-view bulk server action. Posts the email to any `mail.thread` model (project task, helpdesk ticket, contact, lead, calendar event, invoice, sale order, etc.) via `record.message_post(...)`, preserving Message-ID, original date, author, and attachments.
- **Archives backfill wizard** (`bf.email.imap.backfill`) — one-shot scan of any IMAP folder (e.g. `Archives/2025`) with optional `SINCE`/`BEFORE` date filters. Idempotent — UNIQUE Message-ID constraint plus existence checks prevent duplicates on re-runs.
- **RFC 2822 thread tracking** — new `thread_root_id` field, indexed, computed from the `References` header (or `In-Reply-To` / `mail.message.parent_id` chain). Smart button "Conversation" on the form view filters `bf.email` by thread root.
- **Auto-replied** — when an outbound row is created with `in_reply_to` matching an inbound row's Message-ID, the inbound is flipped to `replied` automatically.
- **New fields on `bf.email`**: `imap_uid`, `imap_folder`, `raw_rfc822` (Binary attachment), `thread_root_id`, `thread_count` (compute).
- **`source` selection** extended with `imap`.
- **`in_reply_to`** is now indexed.
- **List view enhancements**: warning decoration on rows without a linked record, inline "Import to chatter" button, badges for `imap` source.
- **Search filters**: `À répondre`, `Sans réponse > 7 jours`, `Dernières 24h`, `Dernières 48h`, `Sans dossier (à router)`, `Avec dossier`, `IMAP orphelin`.
- **Group-by-thread** in search view.
- **`models/bf_email_imap.py`** — reusable RFC 2822 helpers (IMAP4_SSL connection, UID search, body extraction, attachment parsing, thread header parsing, NUL-byte scrubbing).

### Changed
- **Default action context** — `bf_email_action` (action 1785) no longer applies `search_default_filter_new=1`. Default view is now the unified inbox sorted by date desc, including read and replied (excludes archived only).
- **`_should_sync(msg)` extended** — no longer just dedup; now actively promotes existing IMAP-orphan rows when a chatter `mail.message` with the same Message-ID arrives, instead of skipping or creating a duplicate.

### Migration
- `migrations/18.0.1.5.0/post-migrate.py` — recursive CTE backfill of `thread_root_id` for existing rows by walking `mail.message.parent_id` chains. Seeds `thread_root_id` from `in_reply_to` or `message_id_header` for rows without a linked `mail.message`.

### Configuration (`ir.config_parameter`)
- `bf_email.imap_host`, `bf_email.imap_port` (default `993`), `bf_email.imap_user`, `bf_email.imap_password` — IMAP server credentials. Empty by default; cron skips silently when unset.
- `bf_email.imap_batch_size` (default `100`) — UIDs fetched per cron tick.
- `bf_email.imap_last_uid_inbox`, `bf_email.imap_last_uid_sent` — per-folder UID watermarks (managed automatically).

### Notes
- Container restart required after the upgrade — Odoo's registry signaling reloads model definitions but does NOT reload Python bytecode for already-running workers.
- Re-routing preserves the original Message-ID, so subsequent gateway projections of the same email won't duplicate the row (UNIQUE constraint enforces this).

## [18.0.1.4.1] — 2026-04-27

### Fixed
- **`_compute_category` AttributeError on tenants without `sale_team`/`purchase`** — `customer_rank`/`supplier_rank` are not always present on `res.partner`. Replaced direct attribute access with `getattr(partner, 'customer_rank', 0)`.
- **FK violation on deleted partners** — `mail.message.author_id` is a raw int FK that Odoo doesn't auto-null when a partner is deleted. Added `partner.exists()` check in `_prepare_email_vals` to prevent `bf_email_partner_id_fkey` errors.

### Migration
- `migrations/18.0.1.4.1/post-migrate.py` re-runs the 1.4.0 backfill so rows that failed under the earlier hardening get a second pass.

## [18.0.1.4.0] — 2026-04-27

### Fixed
- **Watermark uses `create_date`, not `mail.message.date`** — the previous filter `("date", ">", last_sync)` advanced past back-dated imports (manual scripts, forwarded threads with original `Date:` headers, cross-tenant imports), permanently hiding them. Caught when an inbound reply imported retroactively never appeared in the module. Now uses insertion time (`create_date`).

### Migration
- `migrations/18.0.1.4.0/post-migrate.py` — backfills missing rows by sweeping `mail.message` records that were skipped by the buggy watermark.

## [18.0.1.3.x and earlier]

Initial chatter projection (`mail.message` → `bf.email`), enrichment fields, OWL dashboard, scheduled-drafts cross-record list, security groups, multi-company isolation. See git history for details.
