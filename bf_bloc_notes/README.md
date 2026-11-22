# BF Bloc-notes

Rich quick notes for Odoo 18, with multi-record links, one-click activity conversion, keyboard shortcuts, and a systray icon.

## License

LGPL-3 — see `LICENSE`.

## Features

### Quick capture
- **Systray icon 📝**: left-click = new note, right-click = list filtered to your notes.
- **Keyboard shortcuts**: `Alt+N` opens the capture dialog, `Alt+Shift+N` opens the list.
- **Auto-link**: when you are on a record (any record open in a form view), the dialog offers a "Link to: Type · Name" checkbox, ticked. Untick it (or press `Alt+L`) to create the note without a link.
- **Image drop-zone**: paste (`Ctrl+V`) or drag an image into the editor — the attachment is created automatically and the image inserted into the note body.
- **Ctrl+Enter** in the dialog saves the note.

### Multi-link (m2m)
A note can be attached to multiple records at once. Model `bf.note.link` (`note_id`, `res_model`, `res_id`); the `res_ref` field remains as the "primary link" for backward compatibility. "Notes (N)" smart button on partner / task / project / lead via the batch mixin.

**Any record with a chatter is a valid target** — every non-transient model carrying `mail.thread`, rather than a closed list of ten. Custom models are selectable just like standard ones.

### Rerouting (`bf.note.reroute`)
A **"Reroute…"** button in a note's header, plus a bulk **"Reroute to a record"** action from the list and kanban views.

- **One search box** (`bf_chatter_target` widget, since 2.9.0): type a name and the picker sweeps every chatter-bearing model at once — no model to choose first. Results are grouped by model with an icon and a context line, and closed records are greyed and struck out.
- **The same box resolves a pasted reference**: an Odoo URL, a bare id (`22299`), an invoice name (`INV/2026/00017`), a shorthand (`task:22299`) or a technical reference (`sale.order:17`) surfaces as an *Exact reference* at the top of the list. The separate "Quick link" field is gone — it was folded into the picker. Odoo 18 URLs resolve through `ir.actions.act_window.path`, so any menu URL works, not only the hardcoded shapes.
- **Mode**: *Replace* moves the note (its current links give way); *Add* leaves it attached to both records.
- Nothing is posted or sent: only the links change. Archived notes stay reroutable.
- A note's "Links" tab, its primary-link field and the activity wizard use the same picker, with no technical model name to type.

### Conversion to activity
From a note's form view, quick header buttons:
- **Today** (D)
- **Tomorrow** (D+1)
- **+2 days** / **+1 week**
- **Customize…** (wizard with date, activity type, assignment, editable summary)

One activity is created per linked record (e.g. a note linked to 3 tasks → 3 activities). Default type: **To-do** (`mail.mail_activity_data_todo`). "Activities (N)" smart button on the note for finding all created activities.

### Hybrid visibility
- Private by default (`is_shared=False`): only the author sees it.
- Toggling "Shared with all internal users" makes it readable by every internal user; only the
  author can still edit. To share with some people only, use **Share…** (below).

### Sharing with named people (18.0.4.6.0, 18.0.4.6.1)
- **Share…** in the form header, or the **Sharing** tab, both for the author only: one line per
  recipient (`bf.note.share`), who is a person, a security group or a Discuss channel, read-only or with
  **Can edit** per line. Sharing notifies nobody and subscribes nobody.
- A recipient reads the note and its whole message thread (internal notes and their files included), and
  can post in it. With **Can edit** they also change the title and the content and attach files; they
  remove only the files they attached themselves. Pinning, color, tags, deadline, "Not for Gen",
  archiving, deleting and sharing stay with the author (and with administrators, who keep what they had).
  Changing the author stays refused to everyone but the superuser.
- A recipient subscribes nobody to the note's thread, themselves included, and schedules no activity on
  it. "Convert to task" from a shared note keeps the back link, the title and the content; the note's
  files go to the task only when the author converts it.
- **Service accounts**: tick *Does not receive shared notes* on the user's form (Preferences). The account
  is then never offered as a named recipient, and a note shared with a group or a channel never reaches
  it. Nothing is ticked automatically: no field of a user record tells a service account from a person
  without false positives.
- The "Shared with me" filter and the "Pinned" action include named shares. On someone else's note the
  list and the form keep the author's own fields read-only.
- Phone API: two more keys per note (`can_write`, `shared_with`), same API number; `shared=False` still
  returns only the caller's notes.

### Not for Gen (18.0.4.3.0)
- **Not for Gen** (`gen_excluded`): any AI pass over notes (an assistant routine that reads
  them) is expected to leave the note aside, even when it is ticked while a pass
  is running.
- Toggled by the crossed-out eye on the card (faint when off, solid when on), in the kanban and on the
  `/notes` page. In the kanban the faint eye SETS the flag and the solid eye clears it
  (`action_gen_exclude`, `action_gen_include`), so a stale card never clears it while trying to set it.
  Also on the form, in the list, in the `/notes` actions menu and as a "Not for Gen" filter. Independent
  of pinning and sharing. Only the author can change it.
- ⚠️ It is a rule those passes follow, not a lock: anyone who can read the note still reads it.

### On the desktop (18.0.4.4.0)
A note you have to open a browser tab for is a note you do not take.
- **Desktop widget support**: the token API recognises the device tokens issued by `bf_desktop_pairing`,
  without depending on it. A tenant that does not install it keeps the phone's tokens and nothing changes.
- **Colleagues' notes, read only**: the list accepts `shared`, and then adds the notes colleagues shared
  with you. Every note already carries `mine` and `shared`, so a client can show them apart.
- **Two routes for a widget that polls**: `notes/stamp` returns a light fingerprint, so a client only asks
  for the delta once it has moved; `notes/ids` returns the identifiers in scope, which is the only way to
  learn that a note has LEFT the scope (a delta cannot announce what it can no longer see).
- The mobile contract and the API number are unchanged; three capabilities are added to the ping.

### Rich text from a toolbar (18.0.4.5.0)
- A note accepts a **named HTML subset**: `p div br span font b strong i em u s del strike ul ol li`, plus a
  `style` carrying only `font-weight`, `font-style` and `text-decoration`. Canonical output is `b i u s`.
- **Two keys, served on demand** (`X-Symbifox-Rich` header): `html`, always sanitised, even for a body the
  server declares it cannot take back, and `rich_editable`, which says whether the client can return that
  body without losing anything. A client that does not ask is served neither key: nothing is added to its
  payload.
- ⚠️ **One thing does change for a client that asks for nothing**: `text`. A formatted note inside the
  subset used to be flattened by `html2plaintext`, which spelled bold as `*bold*` and ran list items
  together. It now reads as plain prose, with bullets shown as `• `. That text also feeds the note's
  title, so a phone that used to display `Des *courses*` now displays `Des courses`. Notes that are
  genuinely rich (outside the subset) still go through `html2plaintext`.
- **Writing**: `_mobile_update` and `_mobile_create` accept `html` instead of `text`, never both, and
  answer `flattened: true` when the HTML received carried more than the subset.
- ⚠️ **`text` and `editable` are still the posted app's contract.** The phone writes plain text only, so a
  formatted note stays displayed there rather than editable. A note saved WITHOUT formatting stays plain
  text, and therefore stays editable on the phone.
- The sanitiser reads effects from the TAGS and from the `style`, because a Qt editor writes bold as
  `font-weight:700`, and it knows a style can CANCEL the parent's (`font-weight:normal`).
- Reading never raises: a separate read ceiling (`MAX_HTML_LECTURE`) serves an empty body marked
  "not editable" rather than failing the whole list for one oversized note.

### On the phone (18.0.3.0.0)
A note taken on a phone has to be written in a second, or it ends up in another app, far from the records it belongs to.
- **Installable `/notes` page**: opens straight on the text field, keyboard up. Works on Android and iPhone, installs to the home screen, and offers a **New** shortcut on a long press.
- **Offline first**: a note typed without a network is kept on the device, marked *To send*, and sent when the network comes back. Each note carries a `client_uuid` drawn by the device, and the server returns the same note every time it is sent again, so a replayed queue never creates a duplicate.
- **Share target**: text or a link shared from another Android app opens the page with the note pre-filled.
- **Quick actions** on every note: pin, reminder today or tomorrow, make it a task (project picker), link to a record (name, number or pasted URL), archive with undo.
- **Plain text on the phone**: a note that carries formatting from the desktop (list, image, bold, link, heading) is read-only on the phone, so its formatting is never lost through a text field.
- **Token API** at `/bf_bloc_notes/mobile/v1` for the Symbifox Mobile app: the same model methods as the page, so both behave identically. Device tokens are those issued by `bf_sms_archive` or `bf_email_management`; an archived or portal user is refused even with a valid token.
- The page shell cached for offline use carries **no user data**; the local queue and cache are kept per user, so a second person signing in on the same device never sees or sends the first one's notes.

### Colors and layouts (18.0.4.0.0)
- **Any color on a note**, through `bf_color`: your swatches, the mobile pastels, or a free `#RRGGBB`. A note's existing 0–11 color is read in the pastel palette the phone already showed; no data is rewritten.
- **Colored tags**, and a readable text color computed for each background.
- **Three layouts** for the notes kanban and the `/notes` page: **cards** (a masonry grid, color as background, Google Keep style), **minimal** (one line per note, color as a stripe) and **list** (detailed rows); **compact** or **comfortable** density; color as **fill** or **stripe**; **group by color**. The choice is a user preference, shared by the web client, the page and Symbifox Mobile; a device can keep its own.
- **Mobile contract**: notes carry `color_hex`, `text_color` and `tags`; `GET/POST /prefs` reads and writes the layout and returns the swatches; the ping announces `features` (`color_hex`, `tags`, `prefs`) so an older app keeps its previous behaviour. The API number is unchanged.

### Attachments from the phone (18.0.3.1.0)
- Photos, PDFs or any file shared to a note from Symbifox Mobile are attached to it, open and can be removed from the phone, and follow the note when it becomes a task (mobile API 2, `/notes/<id>/files`).

### Views
- **Kanban**: colored cards (color picker), built-in pin button, body snippet, tags, primary link, deadline.
- **List**: direct pin toggle, filters "My notes / Pinned / Shared with all internal users / Shared with me / Linked / Overdue".
- **Calendar**: if you set a `deadline_date`, the note appears in your calendar (not conflated with an activity).
- **Form**: HTML editor, "Links" tab with a sequence handle for reordering.

### Security
| Risk | Mitigation |
| --- | --- |
| RPC injection on `quick_create_from_context` | Explicit key whitelist (`name`, `body`, `tag_ids`, `pinned`, `color`, `deadline_date`, `is_shared`, `res_model`, `res_id`, `link_ids`). `user_id` is forced to `env.user.id` regardless of payload. |
| Model enumeration via `Reference` | `_selection_target_model` only offers non-transient models carrying `mail.thread`, and skips orphan `ir.model` rows (uninstalled module). The `ir.config_parameter` `bf_bloc_notes.reference_models` remains available as an allowlist to narrow it further. The picker grants no access: the target record's ACLs and `ir.rule` fully apply on read (`_browse_if_allowed`, `_compute_res_name`). |
| Unreachable reroute target | `bf.note.reroute._browse_if_allowed` requires the record to exist, to be in the selection **and** to pass `check_access("read")` — otherwise the quick link returns `None` rather than confirming a record exists. |
| AccessError on the target record | `bf.note.link._compute_res_name` runs `check_access_rights("read")` + `check_access_rule("read")` as the calling user — no `sudo()` — and falls back to `False` on AccessError. `action_open` / `action_open_record` validate access before returning the `act_window`. |
| Note visibility | `ir.rule` records combined in OR: read (author OR `is_shared`), read of a named share, write/create/unlink (author only), write of a named share (title, content, the two follow-up lists — guarded field by field in `write`), administration. The original rules were born `noupdate`, so named sharing ADDS two rules (read only, write only) instead of redefining them. |
| Shares of a note | `bf.note.share` lines are read by the note's author and by their own recipient only; a recipient does not learn who else reads the note, and the recipient count never crosses the read rule. |
| Smart-button N+1 | `bf.note.link.mixin` uses batched `read_group` — 1 query for 200 records. |

### Performance
- `bf_note_count` computed in a single `read_group` query — no N+1 on list views / kanban.
- `res_name` stored (compute store=True) on `bf.note.link`, not recomputed on each render.
- Tracking of activities/tasks born from a note via two dedicated m2m fields (`tracked_activity_ids`, `tracked_task_ids`) instead of an expensive join on `mail.activity`.

## Architecture

```
bf.note ──┬── link_ids ──> bf.note.link ──(res_model, res_id)──> any mail.thread record
          ├── tag_ids ──> bf.note.tag
          ├── activity_ids (m2m) ──> mail.activity (on the target record)
          └── bf.note.reroute (transient) ──> moves / adds the links

bf.note.link.mixin (AbstractModel)
   └─ inherited by: res.partner, project.task, project.project, crm.lead
        └─ adds: bf_note_count (batch), action_open_bf_notes
```

## Dependencies

- `web`, `mail` (always present)
- `project` (smart button + form heritage on `project.task`, `project.project`)
- `crm` (smart button + form heritage on `crm.lead`) — Odoo Community
- `contacts` (smart button + form heritage on `res.partner`)
- `bf_onboarding_base`, `bf_chatter_target` (Symbifox)
- `bf_color` (free colors, swatches and per-user resolution, since 18.0.4.0.0)

## Configuration

- **Models available in `Reference`**: by default, every non-transient model carrying `mail.thread`. The `ir.config_parameter` key `bf_bloc_notes.reference_models` (CSV) acts as an optional allowlist — set it to restrict the picker to exactly those models, leave it empty to expose all of them. The everyday targets (`res.partner`, `project.project`, `project.task`, `crm.lead`, `helpdesk.ticket`, `calendar.event`, `account.move`, `sale.order`, `purchase.order`, `hr.employee`) are listed first.
- **Seeded tags**: Idea, To-do, Reference, Draft (created once, `noupdate=1`).

## Tests

```bash
odoo -d <db> -u bf_bloc_notes --test-enable --test-tags /bf_bloc_notes --stop-after-init --http-port=0
```

About 300 tests cover: auto-title, multi-link, batch count, RPC whitelist, private/shared visibility (read + write), per-link activity creation, unlinked-note guard, compatible-model selection (+ allowlist), rerouting (replace / add / bulk / idempotence / archived note), quick-link resolution (technical reference, shorthand, bare id, Odoo 18 URL, legacy `/web#` URL, incompatible target), the `target_ref` picker on the link row, and, since 18.0.4.6.0, named sharing (the three kinds of recipient, write per line, scope under a `base.group_system` account, attachments of a shared note, the screen's domains, files carried by a task, read-only list and form, the thread, service accounts, the Share… button).

## Changelog

### 18.0.4.6.1
- **Share…** button in the form header (author only): a dialog that names a recipient and creates the
  same share line as the Sharing tab, with a readable refusal for anyone else.
- The old switch is now labelled **Shared with all internal users**, and its help points to Share…; the
  search filter and the card's tooltip follow.
- End migration: an upgrade never overwrites an existing translation, so the French label and help of
  `is_shared` are refreshed only when they still carry the previous text, word for word.

### 18.0.4.6.0
- **Named sharing**: `bf.note.share`, one line per recipient (person, security group, Discuss channel),
  read-only or with write per line. See "Sharing with named people" above.
- **Service accounts**: `res.users.bf_note_no_shared_notes` ("Does not receive shared notes"). The
  migration copies the legacy `bf_bloc_notes.comptes_exclus` parameter when a database had set it, and
  ticks nothing otherwise.
- **Attachments**: the owner guard applies on every path (`ir.attachment` unlink and write, the chatter's
  delete button included), not only on the phone.
- **Thread and activities**: a recipient cannot subscribe others, or schedule or move an activity, on
  someone else's note. Consequence: "Convert to activity" on someone else's note without a link now
  refuses, since the activity would land on the note itself.
- **Screen**: "Shared with me" filter, a separator in the search view, author read-only on the form,
  "Shared" ribbon in front of "Pinned", `aria-pressed` on the card's two toggles, and the author's own
  fields read-only on someone else's note in the card, the list and the form.

### 18.0.4.5.0
- **Rich text:** a named HTML subset on the note, with `html` (always sanitised) and `rich_editable`
  served on demand through the `X-Symbifox-Rich` header, `html` accepted on create and update in place of
  `text` (never both), `flattened` reported when the body received carried more than the subset, and
  `rich_text` announced in the ping's `features`.
- **Changed for every client**, including those that ask for nothing: `text` no longer carries the
  `*bold*` markers `html2plaintext` produced for a note inside the subset, and list items now show as
  `• `. The note's title is derived from that text, so it changes with it.
- **Security:** `_mobile_browse` now applies `_mobile_scope`. `check_access` alone let a
  `base.group_system` account read AND write any note by its identifier, and identifiers are sequential.
- **Robustness:** reading a body never raises. The write ceiling used to be applied to reads, so a single
  oversized note returned 400 for the whole list, on both doors.
- The "editable" judgement crosses four instruments, one of them a witness with no parser: libxml2
  truncates deep nesting, so an element hidden under hundreds of `<div>` is in no tree at all.

### 18.0.4.4.0
- **Desktop:** device tokens from `bf_desktop_pairing` are recognised (without depending on it), the list
  accepts `shared` and then adds colleagues' shared notes read only, and two new routes hold a widget's
  synchronisation: `notes/stamp` (a light fingerprint) and `notes/ids` (what a delta cannot announce,
  namely a note that left the scope).
- **Privacy:** a link whose record the reader cannot open now hides its model, id and URL as well as its
  name. All the keys stay present but empty, plus `restricted: true`, so a posted app does not trip.
- The mobile contract and the API number are unchanged.

### 18.0.4.3.0
- **Not for Gen:** a `gen_excluded` flag on the note, with an eye button on the card (kanban and `/notes`),
  the form, the list and a filter. Mobile API: `gen_excluded` on the note, accepted on create and update
  as a JSON boolean only (a missing key leaves it unchanged, so an older app never clears it), actions
  `gen_exclude` / `gen_include`, and `gen_excluded` announced in the ping's `features`.

### 18.0.4.2.0
- **Security:** the name of a linked record is no longer stored. It is computed under the access rights
  of whoever reads the note, as the mobile API already did: on a shared note, a colleague used to read the
  name of a record only the author could open. Searching notes by that name only looks at records the
  searcher can read. The `res_name` columns stay in the database, unused, so a code rollback without
  `-u` keeps working.
- **Security:** an activity created on a linked record no longer copies the note's body (anyone who reads
  that record reads its activities). It carries a link to the note instead; an activity on the note itself
  keeps the body. The activity wizard still shows the text before sending it.
- **Security:** tags have an owner. A tag a person creates is theirs: others neither see, rename nor
  delete it, and its owner cannot be changed except by an administrator. The module's tags, and every tag
  that existed before this version, stay shared with the whole instance (a migration makes sure of it),
  and only administrators manage those.

### 18.0.4.1.0
- **Quick capture:** the "Linked to" banner becomes a "Link to" checkbox, ticked by default. Unticked
  (click, or `Alt+L` from any field of the dialog), the note is created without a link: the reminder
  lands on the note itself and "Create a task" no longer pre-fills the parent, project or customer.
- **Fix:** the banner always showed the technical name ("project.task #1") because `name_get` no longer
  exists in Odoo 18. It now shows "Type · Name" (`ir.model.display_name_for` and `display_name`, under
  the user's own access rights).
- **Fix:** the record offered is the one DISPLAYED (`currentState.resId`). After "Next", "New" or
  Duplicate, Alt+N still offered the record first opened (`props.resId`, frozen when the form opened).
  On a record not yet created, no link is offered.
- **Fix:** the dialog's checkboxes get ids of their own; with two dialogs stacked, a label ticked the
  other dialog's checkbox.

### 18.0.4.0.0
- **Colors:** free colors through `bf_color`, swatches, colored tags, readable text color.
- **Layouts:** cards, minimal and list, compact or comfortable, fill or stripe, group by color; a user preference shared with the `/notes` page and the mobile app.
- **Mobile API:** `color_hex`, `text_color`, `tags`, `/prefs` and `features` at the ping, backward compatible.

### 18.0.3.1.0
- **Attachments:** files shared to a note from the phone are attached, readable on the phone and carried over when the note becomes a task.

### 18.0.3.0.3
- **Security:** the "Notes" counter on a record depends on the current user
  (`depends_context('uid')`): a count computed as superuser in the same
  transaction is no longer served to another user.
- **Security:** a note link (`bf.note.link`) cannot be moved to another note
  outside superuser.

### 18.0.3.0.2
- **Security:** the author (`user_id`) of a note cannot be changed outside
  superuser.
- The `18.0.3.0.1` migration also recomputes `bf.note.res_name` (stored), not
  only the link names.

### 18.0.3.0.1
- **Security:** the "Notes" counter of a shared record (contact, project…) is
  counted with the user's rights, so it matches what the button opens and no
  longer reveals how many private notes are attached.
- **Security:** the name of a linked record (`bf.note.link.res_name`, a stored
  field) is computed under the rights of the **note's author**, and the mobile
  API resolves it under the rights of the **caller**. A migration recomputes
  the names already stored.
- Tests: `tests/test_isolation_menage.py`, `tests/test_isolation_mobile.py`,
  `tests/test_isolation_adverse.py`.

### 18.0.3.0.0
- **The note pad on the phone**: installable `/notes` page, offline queue, Android share target, quick actions, and a token API for Symbifox Mobile (see *On the phone*).
- **Quick actions on the kanban card** at the desk: reminder today, reminder tomorrow, task, link, archive — without opening the note.
- New field `client_uuid` (unique per author) for idempotent creation from a device.
- A failed request is rolled back before answering: a route that *returns* an error is otherwise committed by Odoo, and a half-done gesture would stay in the database.

### 18.0.2.10.0
- **English source strings, French in `i18n/fr_CA.po`.** Odoo never translates into `en_US`, the source language: while the strings were written in French, an English-speaking user read this module in French.
- **Action titles and notifications are translatable** ("Convert to task", "Task created", the reroute summary): they were plain French strings in Python.
- The accent colour reads the active company's brand (`--brand-primary`) instead of a fixed hex value, like `bf_dark_mode` 18.0.1.4.2.
- Upgrading reloads the French catalogue; the four shipped tags and the onboarding panel switch to English only where they still carry the shipped text, so a tag renamed by hand keeps its name.

### 18.0.2.9.0 (2026-08-17)
- Changed: the reroute wizard, the note links and the primary link now use the shared **`bf_chatter_target`** picker instead of this module's own copy of the target list and URL resolver. One search box for any chatter-bearing record: results grouped by model, no model to pick first, and a pasted Odoo URL, bare id, shorthand (`task:22299`) or technical reference surfaces as an "Exact reference" at the top of the list.

### 18.0.2.8.0 (2026-08-07)
- Added: the **`bf.note.reroute`** wizard — reroute a note (or a selection) to any compatible record, replacing or adding links. Header button + bulk action on the list and kanban views.
- Added: a "Quick link" field resolving an Odoo URL (v18 scheme via `ir.actions.act_window.path`, and the legacy `/web#model=…&id=…`), a bare id, an invoice name, a shorthand (`task:22299`) or a technical reference (`sale.order:17`).
- Changed: `_selection_target_model` now covers **every non-transient model carrying `mail.thread`** instead of a closed list of ten. Direct consequence: notes already attached to a custom model regain a readable "primary link", where `res_ref` silently fell back to `False`. `bf_bloc_notes.reference_models` becomes an optional allowlist.
- Added: a model + record picker (`target_ref`) on `bf.note.link`, to edit a link without typing the technical model name.
- Fix: an activity is no longer created on a record without `mail.activity.mixin` (`calendar.event`, `discuss.channel`, `blog.post`…), where it existed in the database without surfacing anywhere; it falls back to the note. The test reads `ir.model.is_mail_activity` — the presence of an `activity_ids` field proves nothing, `calendar.event` has one without carrying the mixin.

### 18.0.2.7.1 (2026-06-21)
- Fix: `_compute_res_ref` validates `res_model` against the reference field's model selection before building the value, avoiding a `ValueError` that could break `web_read` when a note points at a model that is no longer installed.

### 18.0.2.5.0 (2026-05-06)
- Security: `bf.note.link._compute_res_name` no longer uses `sudo()`; ACLs applied via `check_access_rights` / `check_access_rule` (prevents leaking `display_name` for unreadable records).
- Security: `action_open` (on link and primary note) validates access before returning the `act_window`.
- UX: Alt+N focuses the title, not the body.
- UX: new secondary "Create a task" button in the quick-create dialog — pre-fills `default_name` / `default_description` (+ project / parent / partner where the context allows) and opens a fresh `project.task` form without creating a note.
- Fix: `getCurrentContext()` (auto-link) now ignores `context.active_id`; auto-links only on a real form view, to avoid creating an activity on the wrong chatter.
- Tests: aligned with the current API (`tracked_activity_count`, fallback creation-on-self) + new ACL test for `res_name`.

### 18.0.2.0.0 (2026-05-02)
- Added: multi-links via `bf.note.link` (m2m to records).
- Added: conversion to activity (quick buttons + wizard).
- Added: hybrid visibility (`is_shared`).
- Added: `deadline_date` + calendar view.
- Added: image drop-zone in the quick-create editor.
- Added: pin/unpin directly from the kanban.
- Security: RPC `quick_create_from_context` whitelisted, `user_id` forced.
- Performance: `bf.note.link.mixin` with batched `read_group` (eliminates N+1 on smart buttons).
- Stack: removed `mail.activity.mixin` (overhead) and `tracking=True` (chatter noise).

### 18.0.1.0.0 (2026-05-02)
- Initial release: `bf.note` + `bf.note.tag`, systray, hotkeys Alt+N / Alt+Shift+N, smart buttons on 4 models.

## Credits

Symbifox Inc — https://symbifox.com

---

<sub>Authored and maintained by Les services de consultation Blue Fox, Inc. AI coding assistants were used as productivity tools during development.</sub>
