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
- **Routing rules (2.0+, Outlook-shaped since 9.11)** — a `bf.email.rule` is *a list of conditions* (all of them, or any of them) *minus a list of exceptions*, then actions. Twenty-one matchable attributes: every address field on its own, subject, body preview, a named RFC 2822 header (folded continuation lines included), attachment count, direction, category, priority, and the module's own computed signals — `is_cc_to_me` means "in Cc **and not** in To", which is what makes "put everything I'm only cc'd on out of the box" a single rule. Two escape hatches (`partner_field`, `odoo_domain`) are the only paths that reach `safe_eval`; every other operator is a plain comparison over a length-capped haystack.
  - **Actions**: category, priority, contact, mark read/unread, move to an IMAP folder (`{YYYY}`/`{MM}` expanded), snooze, out of the inbox, hand the row to a colleague, forward.
  - **Ordering**: `sequence, id`; the first rule to claim a target wins, `stop_processing` cuts the walk. A rule with **no** condition never fires — the other reading turns a half-written rule into a mailbox-wide action.
  - **Scope**: personal by default; a rule with no owner applies to every user of its company, is readable by all and writable only by `base.group_system`.
  - **Quick create**: « Règles courantes » offers twelve ready-made recipes and greys out the ones already installed. Same catalogue as the per-user seeding, so improving a recipe improves both.
  - **Test before you trust**: « Essayer » lists what a rule would catch without touching a single message.
  - **Rules fire on arrival, so backfilling is explicit (10.1+)**: « Appliquer cette règle maintenant » runs *that one rule* over exactly the messages « Essayer » just listed — mail already out of the box included, without which a rule that only files or reclassifies could never be applied after the fact. It acts as the rule's **owner** (the admin read-all rule grants no write), and never forwards. « Rejouer toutes les règles » remains the whole-rule-set pass over the open inbox; « Appliquer mes règles » is the same thing on a hand-picked selection in the list. The « Règles courantes » picker offers the backfill on the way in.
  - **A rule that would do nothing says so**: no action at all is flagged on the form and greyed in the list, the mirror of « a rule with no condition never fires ». A `set_folder` the owner's account cannot honour (no `writeback_archive`) names that account instead of only whispering to the log.
  - **Destination folders are created (10.1+)**: `set_folder` names an IMAP folder in a text field. A refused `COPY` was correctly guarded — the message is never destroyed — but the row was already marked handled, so Odoo announced a filing that had not happened. The folder is now created and **subscribed** (most servers hide an unsubscribed folder) and the `COPY` retried once. The recovery sweep re-files where the *rules* asked rather than defaulting every stray to the archive.
- **Automatic forwarding (9.11)** — internal or external, from *your* address (never spoofing the original sender, which would fail SPF/DKIM), original sender in Reply-To. Every guard is answered before a `mail.mail` exists: `Auto-Submitted` messages are never relayed, a message already stamped by this instance breaks the loop, a hop ceiling and a per-rule daily cap bound runaway, your own addresses are never a destination, and an out-of-organisation recipient needs a box only an administrator can tick (`bf_email.internal_domains` says which domains count as inside). Every send **and every refusal, with its reason**, lands in `bf.email.auto.log`, pruned after `bf_email.forward_log_retention_days` (180).
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
- **Real IMAP folders under the inbox (11.1+)** — a collapsible *Dossiers IMAP* group, second in the rail, mirroring the tree the mail server actually reports (`LIST`), subfolders included. What it lists is still `bf.email` rows, not an IMAP session: opening *Archives/2026* shows the mails filed there with their full toolbar and their link to the record they belong to. Reading the folder live off IMAP would produce messages with no row, hence nothing to file them to — that is what the separate IMAP browser is for, and it is still there. The tree is cached on `bf.email.account` (`folder_cache`, `folder_cache_date`, TTL `bf_email.folder_cache_minutes`, default 60 min) so the rail never opens a connection on render, and an unreachable server serves the last known tree instead of taking the inbox down with it. Counts come from two `_read_group` calls rather than one `search_count` per folder: `imap_folder` is not indexed and the rail reloads on every action. **Admin switch**: Settings → Gestion des courriels → uncheck to remove the group base-wide, for an organisation that judges server-folder browsing a distraction from filing mail onto records.
- **Composer (9.1+, target picker 9.2+)** — a new email attached to nothing, with an optional « Classer dans » field carrying the same `bf_chatter_target` picker as the rest of the instance. Pick a record and the composer is *retargeted before sending*, so the message is born on the right chatter with its followers and thread rather than being moved afterwards; the same hook covers scheduled sends, which read `model`/`res_ids` too. Leave it empty and nothing changes. ⚠️ `subject` and `body` are stored computes depending on `model`/`res_ids`: retargeting fires `_compute_body` (which blanks the body with no template) and `_compute_subject`, so both are read before and rewritten after, in a *separate* write — an explicit write removes the field from the recompute queue, which a write grouped with its own dependency does not guarantee. Odoo always posts on a record, so the created `bf.email` row is its own thread, the same trick `_composer_target` already uses for IMAP orphans; the row lands in *Sans dossier* and « Router… » files it later. The shell is born handled so an abandoned composer leaves nothing behind, and `inbox_close_compose` either adopts it (subject, recipients, Message-ID copied from the posted message) or deletes it.
- **Full preview toolbar** — Répondre, Répondre à tous, Transférer, Traité / Remettre en boîte, Reporter, Activité, Router… (with quick targets), Ajouter ▾, Fil, Dossier, .eml, Ouvrir. Every one of them delegates to the existing record method through a server-side allow-list.
- **Router… / Re-router… (9.3+)** — the label follows the mail: *Router…* while it is filed nowhere, *Re-router…* once it is, with the current folder recalled at the top of the menu. The two gestures differ — the second **moves** the message out of the chatter it sits in — and one label for both read as the first. Re-routing an already-chattered mail moves the existing `mail.message` instead of posting a copy: a copy would leave the original on the wrong record, which is the very thing being undone, and two chatters would carry the same Message-ID. Attachments follow, the old record gets a note, and pulling a message out requires **write access on that record** — otherwise being able to read a mail would be enough to take it out of a colleague's folder.
- **Target suggestion follows the thread (9.3+)** — the reroute wizard pre-fills with the record another mail of the same RFC 2822 thread is filed under, which is a far steadier signal than "this contact has exactly one open task". Every selected row must share the *same* root, the folder already held is excluded, and a record the user cannot write is never offered.
- **Ajouter ▾ (9.3+)** — Tâche, Piste, Ticket, Dépense, Facture fournisseur, Facture client, created *from* the mail and importing it into the new record's chatter. Same methods as the form view's « Nouveau ▾ », so the two cannot drift; entries for apps that are not installed are not offered, and a batch is refused with a readable message rather than a bare `ensure_one`.
- **Brouillons folder (9.3+)** — my `mail.scheduled.message` rows, soonest first (the reverse of mail: on sends still to come, what matters is the next one out). Preview, *Envoyer maintenant*, *Modifier*, *Ouvrir le dossier*, *Annuler l'envoi*, and an overdue date shown in red. It is the only folder in the rail whose source is not `bf.email`: a scheduled send has neither Message-ID nor IMAP counterpart, and minting a `bf.email` row so it could show here would make it a fake mail in every count — so the list swaps source per folder, keeping one output contract. Scope is *mine*, not *what I can see*: the model's own access already spans records I can post on, which would surface a colleague's draft on a task we both follow.
- **Server-side search** across the whole folder (subject, from, to, body preview) — the source is an indexed table, so there is no reason to filter only the loaded page the way the IMAP browser has to.
- **Drag a row onto a folder** — Traités marks it handled, Reportés opens the snooze wizard, Boîte de réception puts it back.
- **Select-all checkbox (9.5+)** — three-state (all / partial / none) at the head of the checkbox column, inbox only. The partial state rides on the DOM `indeterminate` *property*, which no `t-att-` can set, hence the `onPatched` hook; without it a partial selection would render as "nothing ticked". It covers the **loaded** rows, not the folder — the list fills on scroll, and claiming to select three thousand mails when a hundred are in memory is a lie that comes due on the first click of « Traité ». The bulk bar spells it out ("N selected of M loaded — T in this folder") as soon as the folder holds more than the current page.
- **Shift-click range selection (11.1+)** — tick one box, hold Shift, tick another: everything between them is selected, in both the inbox and the IMAP browser. The range walks the **displayed** order, so a live search filters it like everything else, and a second Shift-click extends the range instead of restarting from the first box. ⚠️ The handler calls `preventDefault()`: the browser flips the checkbox *before* OWL repaints, and when the computed state happens to match the previous one (re-ticking an already-ticked box caught in a range) the attribute does not change, OWL skips that node, and the box renders unticked while still counting as selected. State is now the checkbox's only source of truth.
- **Collapsible action ribbon (11.1+)** — a chevron in the preview header shrinks the button row to a single line of icons. The header itself — subject, De, À, date, folder, attachments — never collapses: that is the message's context, not an option. No action disappears; tooltips and keyboard shortcuts carry the meaning. Stored with the other display preferences and shared by both screens.
- **Column selector (9.4+)** — a table button next to the layout controls picks which columns the list shows: Date, Correspondant, Dossier, Catégorie, Extrait, État (IMAP browser: Date, Expéditeur, État). Kept in the browser with the other preferences, and stored *per screen* — the two column sets genuinely differ, and one shared key would force each screen to filter out what does not concern it. **Subject cannot be unticked** (shown ticked and disabled): without that floor, unticking everything builds an empty list with nothing left to say how to get out of it. *Catégorie* reuses the labels `inbox_get_folders` already computes for the folder rail rather than querying per row, so the column and the folder necessarily say the same word; *Extrait* surfaces `body_preview`, until now only a tooltip.
- **Preview pane right or bottom (9.3+)** — `paneLayout`, with a draggable splitter and a size remembered *per layout*: the height you would give an email body is not the width you would give it. Two toolbar buttons plus a preferences entry, and it applies to the IMAP browser too.
- **Compact density means one line (9.3+)** — it used to only add `table-sm`, which tightens padding without stopping a cell from wrapping, and wrapping is exactly what happens once the preview moves to the right and the list narrows. Compact now forces `table-layout: fixed` (without it the browser widens the column to fit and the ellipsis never triggers), `nowrap` and ellipsis on every cell, badges included. Column widths tighten with a right-hand pane as well, or the Subject column — the one you actually read — gets crushed.
- **Shared preferences** with the IMAP browser (`bf_email_ui_common.js`): storage key, date format, sender rendering, preview scaffold and pane layout all live in one place, so the two screens cannot drift apart.
- **Keyboard** — `J`/`K`/`↑`/`↓` navigate · `R` reply · `Shift+R` reply-all · `F` forward · `E` Traité · `Y` router · `H` reporter · `T` activité · `O` dossier · `C` composer · `S` or `/` search · `Escape` cancel.

The list view is still there under **Boîte de réception (liste)**: filters, group-by, pivot, export and server actions are things a client action does not provide, and there was no reason to lose them.

### Chatter handled indicator (9.0+)
Every chatter message carries a badge — **À traiter**, **Traité** or **Reporté** — reflecting the current user's `bf.email` mirror. `mail.message._to_store` joins `bfEmailState` in one query per rendered batch, and only on the `for_current_user` path: the state is strictly personal and must never ride along in a broadcast. The message actions follow the same state, so « Traité » disappears once the mail is out of the inbox and « Remettre en boîte » only shows where it means something.

### Arrival notice (11.5+, buttons and 30 s cap in 11.6)
A mail lands, a toast shows up in the open tab. This is the **second transport for
the same news**, alongside the mobile push in `push_transport.py`; both are fed by
the *same* sweep of fresh rows inside `_sync_account`, so they cannot drift apart.

Three levels of setting, widest to finest:

- `ir.config_parameter` **`bf_email.popup_enabled`** — the instance, with a
  checkbox under Settings → Gestion des courriels. **Absent means no**, so a
  database that receives this version at its next `-u` does not change behaviour.
- **`bf.email.account.popup_mode`** (`none` / `transient` / `sticky`, default
  `transient`) — the person. The account already carries `user_id`, so setting it
  per account *is* the per-person setting; a second field on `res.users` would say
  the same thing twice and eventually say it differently.
- **`bf.email.account.popup_sticky_folders`** — the folder. A comma-separated
  list, case- and space-insensitive. What lands there gets the full 30 seconds
  instead of 8. The field **narrows** attention, it never switches anything back
  on: it has no effect when the account is set to `none`.
- **`bf.email.account.popup_snooze_minutes`** (default 60) — what the *Reporter*
  button does. Bounded to `[1, 43200]`: zero would mean a deadline already in the
  past, which `mobile_snooze` refuses.

Past five notices of one kind in a single pass, a summary replaces the pile — a
catch-up after downtime must not stack the mailbox on screen. Sticky and transient
are counted separately, otherwise a batch of transients would drown the few
stickies on a recovery day.

⚠️ **The bus payload carries an id and nothing else.** `bus.bus._sendone`
broadcasts to the *partner*, one partner can carry more than one user, and the bus
never consults a record rule. The client re-reads the row through the ORM, which
does apply them; a row the reader may not see comes back empty and produces no
notice — that is the intended behaviour, not an error path. Same reasoning as
`_broadcast_change`, and the reason the `bf_email/changed` channel carries only a
`reason`. A test forbids adding the subject, so "let's avoid the round-trip" fails
instead of shipping.

A mail handled elsewhere — another tab, the phone, a rule — closes its own
notice, off the `bf_email/changed` tick. The `calendarNotification` service is
**not** overridden: calendar reminders keep their popup and their snooze buttons,
mail notices simply sit next to them.

**Three buttons (11.6)** — *Ouvrir*, *Reporter*, *Traité*. The last two go through
`bf.email.popup_snooze` and `bf.email.popup_mark_handled`, which **delegate** to
`mobile_snooze` / `mobile_set_handled`. Delegation is the point: "handled" must
mean exactly the same thing in the notice, in the inbox and in the app — IMAP
write-back to `Archives/{YYYY}` and clearing the phone notification included.

⚠️ Both methods are public, hence callable over XML-RPC by any logged-in account
with any id it cares to guess. `_mobile_browse` is what refuses somebody else's
row, and it is reused as-is rather than duplicated: a `group_email_admin` member
can read every mailbox, and a second check written apart would end up saying
something different from the first.

**Snoozing re-announces (11.6)** — `_cron_imap_mirror` already woke rows whose
`snoozed_until` had passed; it now asks for the notice again, flagged `wake`, so
the toast reads "report échu" rather than announcing an arrival. Without it,
*Reporter* would make a mail **disappear** rather than defer it. Only the popup is
replayed: mobile push keeps its own, separate switch.

**Hard 30-second cap, across all windows (11.6)** — 8 s transient, 30 s sticky.
"Sticky" therefore no longer means "until a gesture"; it means "the full thirty
seconds".

⚠️ `autocloseDelay` caps nothing. The stock template calls `freeze` when the
pointer enters the stack and `refresh` when it leaves, and `refresh` RESTARTS the
delay in full — a "30000 ms" stretches indefinitely under the mouse. The notice is
declared `sticky` (which neutralises that) and our own timer decides.

The countdown starts from `sent_ms`, the **server** clock at send time, never from
display. Two open windows show the same notice and extinguish it at the same
instant; opening a third lengthens nothing. Measured in a browser: 29 800 ms in
both windows, 11 ms apart at both ends.

The same arithmetic fixes an 11.5.0 defect: `bus.bus` keeps its messages for **24
hours** (`bus.gc_retention_seconds`) and replays them on reconnect, with
`last_notification_id` surviving in localStorage — so reopening the browser the
next morning used to dump every notice from the day before, all at once. A replayed
notice now arrives expired and never shows.

⚠️ Accepted trade-off: a workstation whose clock runs more than thirty seconds
**ahead** of the server will see no notice at all. Because that is exactly the kind
of failure that lies "all is well", the service counts discarded notices and emits
one `console.warn` at the fifth if none was ever displayed.

**Known limit** — the notice only covers mail ingested **over IMAP**. A row coming
from a chatter or the mail gateway has no `account_id`, hence no setting to read,
hence no notice.

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

### Inbox RPC surface (9.0+)
The inbox client action calls these `@api.model` methods on `bf.email`. They stay in the current user's environment — no `sudo` anywhere — so record rules remain the authority and a colleague's mailbox can never surface.

- `inbox_get_folders()` — `[{key, label, icon, parent, selectable, count, unread_count}]`. A parent (`selectable: False`) sums its children.
- `inbox_get_messages(folder, offset, limit, search)` — `{folder, messages, total, offset, limit}`, newest first, page size capped at 500. Unknown or non-selectable folder keys raise.
- `inbox_get_body(email_id)` — sanitized `body_html_display`, headers and attachments, and flips `new` → `read` for rows the user may actually write.
- `inbox_run_action(action, email_ids)` — named actions only, through `_INBOX_ACTIONS`: the method name comes from the browser, so the server decides what is nameable. Window actions returned here get an explicit `views` key, because `call_kw` — unlike `call_button` — never runs them through `clean_action()`, and without it the web client maps over `undefined` and the dialog silently never opens.
- `inbox_compose()` / `inbox_close_compose(shell_id)` — open the composer on a fresh unattached row, then adopt or delete that row when the dialog closes.
- `_inbox_imap_folder_defs()` / `_inbox_imap_counts(defs)` (11.1+) — build the IMAP subtree from each account's cached `LIST` and count its rows in two grouped queries. Neither opens a connection; `bf.email.account.get_imap_folders(force=False)` owns the cache and the refresh.
- `_inbox_folder_domain(key)` resolves `imapf:<account>:<folder>` and `imapacct:<account>` keys **without** going through `_inbox_folder_defs` (11.1+): the domain is needed on every folder click and every page, and routing it through discovery would put a cache refresh — so, one day in two, an IMAP round-trip — behind a click. Account ownership is verified against the caller, never inferred from the key.
- `inbox_sync_now()` — same work as the list view's *Synchroniser maintenant*, but returns only the notification text. `action_sync_now` ends with `next: {tag: reload}`, which reloads the whole web client and would throw away the open preview and selection.

### IMAP write-back sweep (9.0+)
`_cron_imap_writeback_sweep(dry_run=False)`, hourly. `_cron_imap_reconcile` and `_cron_imap_mirror` both run server → Odoo; this one closes the other direction. It reads the Message-IDs actually sitting in INBOX, resolves each owner's row, and replays `_imap_writeback_archive` on the ones already marked handled — covering both a write-back that failed on a transient error (nothing ever retried it) and any code path that sets `is_handled` without going through `action_archive`. Rows born from the chatter carry no `account_id` and would be skipped by the write-back; since the sweep has just observed their physical copy in that account's INBOX, it binds them to it first. Bounded by INBOX size, idempotent, and `dry_run=True` turns it into a plain gap report.

### IMAP write-back UID verification (11.1+)
`_imap_writeback_archive` used to trust the row's `imap_uid` whenever `imap_folder` said INBOX. A UID only means something **inside its own mailbox**: `UID COPY` aimed at an absent UID answers **`OK`** while copying nothing (RFC 3501 — UID commands silently ignore unknown UIDs), the `STORE \Deleted` that follows marks nothing, and the row then records an archive that never happened. Odoo says handled; the mail is still sitting in the real inbox. The COPY-status guard added in 6.7.0 cannot catch this case, because the server genuinely answers `OK`.

The fast path now verifies the UID (`UID FETCH … BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)]`) before using it and falls back to the header search when it does not match, leaving an `INFO` line behind — there was no signal at all before. `_imap_writeback_restore` also stops inheriting the archive folder's UID when it cannot locate the message back in INBOX (the field is cleared instead), which is what minted those stale UIDs in the first place.

### Sweep across two mailboxes (11.2+)
`_cron_imap_writeback_sweep` resolves rows by `user_id`. One person can own two `bf.email.account` rows pointing at two different mailboxes, and an address delivered to both leaves **two physical copies** while a single `bf.email` row accounts for one of them. The sweep therefore sees, in account A's INBOX, a copy whose row follows account B — and used to hand the repair to `_imap_writeback_move`, which reconnects to the *row's* mailbox (B), finds nothing there, and returns. The message stayed in A's INBOX for good, replayed hourly with no effect and no log line.

`_imap_writeback_move(folder_template, account=…)` now acts in the mailbox **where the copy was observed**, using that mailbox's archive folder. ⚠️ The row is *not* rewritten in that case: its `imap_uid` / `imap_folder` describe its own copy, in its own mailbox, and overwriting them with a UID from elsewhere would mint exactly the stale UID the 11.1 guard exists to catch. The move leaves an `INFO` line naming both accounts.

### What a renamed or deleted IMAP folder does (11.3+)
`imap_folder` is written at ingestion, on archive and on restore, and **never** reconciled against what the server actually holds. So renaming or deleting a folder server-side costs nothing in content — subject, body, attachments and chatter live here, not there — and breaks only the pointer.

- Rows keep their old folder name for ever. They stay reachable through *Tous les courriels*, the category folders, search and their own form.
- The folder rail is built from the **union** of the server's `LIST` and the folder names the user's rows still cite; the latter carry a broken-chain icon and an "absent du serveur" tooltip, so the drift is visible rather than hidden.
- A restore whose source folder is gone (or whose message is no longer in it) now calls `_imap_forget_location()`: `imap_uid` and `imap_folder` are cleared, `imap_in_inbox` stays false. Before that, the row left *Traités* and entered no working list at all.
- Archiving toward a deleted archive folder **recreates it** (`ensure_folder`). Deleting the archive folder in the webmail does not stop the write-back.
- `_cron_imap_mirror` only walks rows with `imap_folder ilike 'INBOX'`, so a row pointing at a renamed folder is outside its reach by construction.

### One definition of "inbox" (11.3+)
It used to live in six copies — folder rail, mobile SQL filter, dashboard action, list-view search filter, window action domain, and the systray badge in JavaScript (`bf_email_systray`) — each carrying a comment asking the other five to stay in step. `bf.email._inbox_domain()` is now the source. The Python copies derive from it; the two that cannot are pinned by tests — the mobile SQL is compared to the domain **over the same rows** (not over its text), and the badge's JavaScript is read off disk and checked for every leaf.

### Mobile API (6.8+)
A REST/JSON surface under `/bf_email_management/mobile/v1/`, consumed by the **Odoo Inbox** Android app (the client is not part of this repository). Full contract in [MOBILE_API.md](MOBILE_API.md); the shape in brief:

- **Auth is a captured web login.** `auth/start` runs at `auth="user"`, so the browser goes through `/web/login` — password, SSO and MFA apply without the app reimplementing any of them. It emits a single-use 3-minute code; the durable bearer token is only revealed at `auth/exchange`, over HTTPS. **No password route is exposed.**
- **Conversations, not rows.** Threads fold on `thread_root_id`, with `id:<id>` for messages carrying no `References` chain. The aggregate runs in SQL and pages properly. The desktop's subject-prefix fallback is not reproduced — it would merge unrelated threads from the same correspondent.
- **Full client.** Read, search, reply / reply-all / forward, compose, archive (with the real IMAP write-back), snooze, plus the Odoo-side verbs: route into a record's chatter, spawn a task / ticket / lead / bill / invoice / expense.
- **Remote content blocked by default.** Bodies are the sanitized `body_html_display` with remote `<img src>` parked in `data-blocked-src` until the reader asks. `cid:` and `data:` sources are untouched.
- **Push over UnifiedPush (ntfy), no Google dependency.** One endpoint per device, registered against this module and `bf_sms_archive` independently; payloads are told apart by `type`.
- **Badges have their own cheap route (11.7+).** `GET /counts?grouped=` returns the mailbox counters alone, light enough to be re-read on every refresh; `grouped` (default true) also rides on `/mark_read`, `/handle` and `/snooze`. Without it the totals only came down when the screen opened, or inside the answer to a mutation made from the phone.
- **Push has an instance kill switch** — `ir.config_parameter` `bf_email.push_enabled`, default `"1"`. Clearing a device's `push_endpoint` does stop the push, but the app re-registers on its next launch and it all comes back; the parameter is the durable off. The in-Odoo notice has its own, separate switch — see *Arrival notice*.

`bf.email.mobile.device` holds the bearer tokens. Devices are minted only by the controller, in `sudo`, and are owner-scoped — see Security below.

### Tests (6.8+)
```bash
odoo -d <base> -u bf_email_management --test-enable --test-tags /bf_email_management
```
406 tests in `tests/`: device-token lifecycle, thread folding and filters, opening a thread, sending and its anti-duplicate guard, the four security boundaries, the routes under `HttpCase`, the inbox RPC surface (scope, action allow-list, `views` key), the IMAP write-back loop, the chatter indicator, and the arrival notice with its thirty-second cap.

⚠️ On a test bench, `--db-filter` **and** `--addons-path` are both mandatory: without the first, `HttpCase` requests land on another database; without the second (together with `ODOO_RC=/dev/null`) the module is not even found and the run reports "0 tests" with no error.

`tools/smoke_mobile_api.py <instance>` exercises a **live** instance after deployment, with an exit code. With `--token` it also checks that the contract's shape still matches what the Android app's models expect — the Kotlin tests' fixtures are frozen and would pass through a server-side drift.

## Security

### Mailbox isolation — the contract (11.2.1 spells it out in tests)
- A `bf.email` row is visible to its **owner** only (`bf_email_rule_owner`, `[('user_id','=',user.id)]`, granted to every internal user).
- Except to whoever carries **Gestion des courriels / Administrateur — tous les courriels** (`group_email_admin`), ticked on the user's own form: `bf_email_rule_admin_all` is `[(1,'=',1)]` **read-only** — `perm_write`, `perm_create` and `perm_unlink` are all False, so seeing everything is never editing or deleting anything.
- `bf.email.account` carries the IMAP password and has **no** admin rule at all — owner only, the email admin included. Do not add one.
- Owner scoping of the *screen* is separate from the record rule: the sidebar, the badge and `inbox_get_*` all pin `('user_id','=',uid)` explicitly. Without that an admin sees "99+" while sitting at inbox zero, counting everyone's mail in their own inbox.
- ⚠️ Any **public** method on `bf.email.account` is a `call_kw` door onto any account id — what protects it is what it touches, not the view it is reached from. `_get_imap_folders` / `_store_imap_folders` are private for that reason, and the latter also checks `write` access before its `sudo()`.



- **Per-user model (4.0+)**: each internal user manages their own `bf.email.account` rows and only sees their own `bf.email` / `bf.email.rule` records. No admin bypass — even a superuser browsing through the UI honours the per-owner `ir.rule` (raw SQL queries in the dashboard also include an explicit `user_id` predicate).
- **Account credentials** (host / login / password) live on the `bf.email.account` row. The `ir.rule` `[('user_id', '=', user.id)]` makes them readable only by the owner.
- **Menu badge** for unread count.
- Re-routing wizard **and** the *Guess & import* bulk wizard validate `check_access_rights("write")` and `check_access_rule("write")` on the target record before posting into its chatter (the bulk path posts through a `sudo` proxy, so the user-level check is explicit — 5.7+).
- **IMAP command hardening (5.7+)** — `imaplib` does not validate its arguments, so every folder name, UID and header value interpolated into an IMAP command is escaped/validated through `bf_email_imap.imap_quote_mailbox` / `imap_uid_token` / `imap_reject_crlf`. CR/LF are rejected outright, so a crafted Message-ID, folder name or UID cannot inject a second command into the authenticated session.
- **No raw email HTML rendered with active content (5.7+)** — inbound HTML is stored raw (`body_html`, only NUL-stripped) but the form Body tab renders a sanitized projection (`body_html_display` = `html_sanitize(body_html)`) and the IMAP-browser preview field is `sanitize=True`. `body_html` is kept raw only for the reply/forward builders, which `html_sanitize` at their own use sites. The OWL browser preview additionally renders inside a scriptless sandboxed `<iframe>`.
- **Mobile devices are outside the admin zone (6.8+)** — `bf.email.mobile.device` stores live bearer tokens, so `group_email_admin` gets no read-all rule on it, the same reasoning that removed `bf.email.account` from the admin zone in 6.0. `device_token` and `pending_code` additionally carry `groups="group_email_admin"` to stay out of ordinary RPC reads, and no group has create rights — only the `auth/start` controller mints devices, in `sudo`. Mobile writes also refuse rows owned by another user: the owner rule alone would not stop an admin's phone from archiving somebody else's inbox, since the admin rule grants read on every mailbox.
- **Mobile redirect allowlist (6.8+)** — `auth/start` only redirects to a scheme listed in `bf_email_management.mobile_redirect_schemes`; anything else is a flat 400. Without it the route is an open redirect handing a live exchange code to an arbitrary URL. Registered UnifiedPush endpoints are checked against private/loopback/link-local resolution **at registration and again at send time**, since DNS can be repointed after the fact.
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

For support, please contact Blue Fox Inc. or open an issue in the repository.

---

Authored and maintained by Blue Fox Inc. AI coding assistants were used as productivity tools during development.
