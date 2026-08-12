# BF Bloc-notes

Rich quick notes for Odoo 18, with multi-record links, one-click activity conversion, keyboard shortcuts, and a systray icon.

## License

LGPL-3 — see `LICENSE`.

## Features

### Quick capture
- **Systray icon 📝**: left-click = new note, right-click = list filtered to your notes.
- **Keyboard shortcuts**: `Alt+N` opens the capture dialog, `Alt+Shift+N` opens the list.
- **Auto-link**: if you are on a partner / task / project / lead form, the dialog pre-fills the link.
- **Image drop-zone**: paste (`Ctrl+V`) or drag an image into the editor — the attachment is created automatically and the image inserted into the note body.
- **Ctrl+Enter** in the dialog saves the note.

### Multi-link (m2m)
A note can be attached to multiple records at once. Model `bf.note.link` (`note_id`, `res_model`, `res_id`); the `res_ref` field remains as the "primary link" for backward compatibility. "Notes (N)" smart button on partner / task / project / lead via the batch mixin.

**Any record with a chatter is a valid target** — every non-transient model carrying `mail.thread`, rather than a closed list of ten. Custom models are selectable just like standard ones.

### Rerouting (`bf.note.reroute`)
A **"Reroute…"** button in a note's header, plus a bulk **"Reroute to a record"** action from the list and kanban views.

- **Quick link**: paste an Odoo URL, a bare id (`22299`), an invoice name (`INV/2026/00017`), a shorthand (`task:22299`) or a technical reference (`sale.order:17`) — the target resolves on its own. Odoo 18 URLs resolve through `ir.actions.act_window.path`, so any menu URL works, not only the hardcoded shapes.
- **Mode**: *Replace* moves the note (its current links give way); *Add* leaves it attached to both records.
- Nothing is posted or sent: only the links change. Archived notes stay reroutable.
- A note's "Links" tab offers the same model + record picker (`target_ref`), with no technical model name to type.

### Conversion to activity
From a note's form view, quick header buttons:
- **Today** (D)
- **Tomorrow** (D+1)
- **+2 days** / **+1 week**
- **Customize…** (wizard with date, activity type, assignment, editable summary)

One activity is created per linked record (e.g. a note linked to 3 tasks → 3 activities). Default type: **To-do** (`mail.mail_activity_data_todo`). "Activities (N)" smart button on the note for finding all created activities.

### Hybrid visibility
- Private by default (`is_shared=False`): only the author sees it.
- Toggling "Shared" makes it readable by all internal users; only the author can still edit.

### Views
- **Kanban**: colored cards (color picker), built-in pin button, body snippet, tags, primary link, deadline.
- **List**: direct pin toggle, filters "My notes / Pinned / Shared / Linked / Overdue".
- **Calendar**: if you set a `deadline_date`, the note appears in your calendar (not conflated with an activity).
- **Form**: HTML editor, "Links" tab with a sequence handle for reordering.

### Security
| Risk | Mitigation |
| --- | --- |
| RPC injection on `quick_create_from_context` | Explicit key whitelist (`name`, `body`, `tag_ids`, `pinned`, `color`, `deadline_date`, `is_shared`, `res_model`, `res_id`, `link_ids`). `user_id` is forced to `env.user.id` regardless of payload. |
| Model enumeration via `Reference` | `_selection_target_model` only offers non-transient models carrying `mail.thread`, and skips orphan `ir.model` rows (uninstalled module). The `ir.config_parameter` `bf_bloc_notes.reference_models` remains available as an allowlist to narrow it further. The picker grants no access: the target record's ACLs and `ir.rule` fully apply on read (`_browse_if_allowed`, `_compute_res_name`). |
| Unreachable reroute target | `bf.note.reroute._browse_if_allowed` requires the record to exist, to be in the selection **and** to pass `check_access("read")` — otherwise the quick link returns `None` rather than confirming a record exists. |
| AccessError on the target record | `bf.note.link._compute_res_name` runs `check_access_rights("read")` + `check_access_rule("read")` as the calling user — no `sudo()` — and falls back to `False` on AccessError. `action_open` / `action_open_record` validate access before returning the `act_window`. |
| Note visibility | Two separate `ir.rule` records: read (author OR `is_shared`), write/unlink (author only). |
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

## Configuration

- **Models available in `Reference`**: by default, every non-transient model carrying `mail.thread`. The `ir.config_parameter` key `bf_bloc_notes.reference_models` (CSV) acts as an optional allowlist — set it to restrict the picker to exactly those models, leave it empty to expose all of them. The everyday targets (`res.partner`, `project.project`, `project.task`, `crm.lead`, `helpdesk.ticket`, `calendar.event`, `account.move`, `sale.order`, `purchase.order`, `hr.employee`) are listed first.
- **Seeded tags**: Idea, To-do, Reference, Draft (created once, `noupdate=1`).

## Tests

```bash
odoo -d <db> -u bf_bloc_notes --test-enable --test-tags /bf_bloc_notes --stop-after-init --http-port=0
```

35 tests cover: auto-title, multi-link, batch count, RPC whitelist, private/shared visibility (read + write), per-link activity creation, unlinked-note guard, compatible-model selection (+ allowlist), rerouting (replace / add / bulk / idempotence / archived note), quick-link resolution (technical reference, shorthand, bare id, Odoo 18 URL, legacy `/web#` URL, incompatible target), and the `target_ref` picker on the link row.

## Changelog

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

Blue Fox Inc — https://bluefoxconsultant.com

---

<sub>Authored and maintained by Blue Fox Inc. AI coding assistants were used as productivity tools during development.</sub>
