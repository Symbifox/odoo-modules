# BF Chatter Timesheet

Odoo 18 Community module that adds a "Timesheet" checkbox to the chatter composer of a `project.task`. When ticked while logging an internal note, an `account.analytic.line` entry is created at the same time, tied to the task — without leaving the form.

## Use case

Logging time on a task in Odoo 18 normally requires a context switch: write the note in the chatter, switch to the *Timesheets* tab, click *Add a line*, retype what you just did, set the duration, save. The cognitive cost of the switch means many quick activities (a 10-minute call, a 5-minute review) are never logged at all.

This module collapses the two actions into one. The chatter note remains the source of truth for *what happened*; the timesheet entry mirrors *how long it took*.

## Features

- **Inline controls** — a single row appears below the textarea in *Log note* mode: checkbox, hour and minute inputs, four duration presets (`5m` / `15m` / `30m` / `1h`)
- **Conditional visibility** — the row only renders when the chatter is on a `project.task` AND the composer is in *Log note* mode (not *Send message*)
- **Description carry-over** — the timesheet `name` defaults to the plain-text version of the note body, capped at 500 characters; falls back to the task name when the body is empty
- **Standard ACLs** — the timesheet is created under the user's own employee record; standard `hr_timesheet` rights are enforced
- **No mode for the message** — internal notes remain pure internal notes; no marker, no follower change, no notification

## Technical architecture

### Structure

```
bf_chatter_timesheet/
├── __init__.py
├── __manifest__.py
├── README.md
├── LICENSE
├── models/
│   ├── __init__.py
│   └── project_task.py
└── static/
    └── src/
        ├── js/
        │   └── chatter_timesheet_patch.js
        ├── xml/
        │   └── chatter_timesheet.xml
        └── scss/
            └── chatter_timesheet.scss
```

### Dependencies

| Module | Role |
|--------|------|
| `mail` | OWL `Composer` component being patched |
| `project` | `project.task` model carrying the new method |
| `hr_timesheet` | `account.analytic.line` model + ACLs |
| `bf_timesheet_timer` | Functional companion (timer-based timesheet entry); declared as a peer to keep the BF timesheet UX coherent |
| `bf_onboarding_base` | Onboarding wizard integration |

No external Python libraries.

### Backend

A single method on `project.task`:

```python
def action_bf_create_chatter_timesheet(self, duration_hours, body_html=""):
    """Create a timesheet line tied to this task from a chatter note."""
```

The method:

1. Coerces `duration_hours` to `float`, raises `ValidationError` if `<= 0`
2. Verifies `self.allow_timesheets` and `self.project_id` (else `UserError`)
3. Resolves the user's `hr.employee` via `env.user.employee_id` with a `search` fallback on `(user_id, company_id in env.companies)`; raises `UserError` if none
4. Strips HTML from `body_html` via `odoo.tools.html2plaintext`, trims, falls back to `self.name`, caps at 500 characters
5. Creates `account.analytic.line` with `task_id`, `project_id`, `employee_id`, `name`, `date=context_today`, `unit_amount`

No `sudo()` is used; the standard `account.analytic.line` ACL applies.

### Frontend

The OWL `Composer` component (`@mail/core/common/composer`) is patched:

- `setup()` extends the base setup to declare a local `useState` for `enabled / hours / minutes` and to inject the `orm` and `notification` services
- `bfTimesheetVisible` getter returns `true` only when `props.type === "note"`, the composer is not editing a message, and `props.composer.thread.model === "project.task"`
- `bfTimesheetSetPreset(minutes)` sets total duration from a preset and ticks the box
- `sendMessage()` is overridden to call `super(...)` first (the note posts as usual), then issues an ORM call to `project.task.action_bf_create_chatter_timesheet([id], duration_hours, bodySnapshot)` when the checkbox is on and the duration is positive

The XML template inherits `mail.Composer` and injects the controls inside `o-mail-Composer-coreMain`. In extended mode (the default for form views) `coreMain` lays out vertically, so the controls render below the textarea and the send button.

### Security review

Pre-publication checklist (per BF policy):

| Concern | Status |
|---------|--------|
| Hardcoded secrets | None |
| SSRF | No outbound HTTP; no URL parsing |
| Command injection | No `subprocess`, no shell calls |
| SQL injection | No raw SQL; all writes via ORM |
| ReDoS | No regex |
| ACL coverage | No new model; relies on `project.task` and `account.analytic.line` standard ACLs |
| `sudo()` | Not used |
| IDOR | Method is bound to the recordset; the user must have read access to the task and create rights on `account.analytic.line`. Employee is forced to the current user — no impersonation |
| HTML in description | Stripped via `html2plaintext` before persistence; output is a plain string |
| Unbounded input | `unit_amount` validated `> 0`; `name` truncated to 500 chars |
| XML ID leakage | No `data/*.xml`, no XML IDs |
| Tenant data | None — only `Blue Fox Inc.` author/website metadata |

### Edge cases

| Case | Behavior |
|------|----------|
| Composer in *Send message* mode | Controls hidden |
| Chatter on a non-`project.task` model | Controls hidden |
| Editing an existing message | Controls hidden |
| Checkbox ticked, duration `0h0m` | Notification *Coche la case mais saisis une durée supérieure à 0*; nothing is sent |
| Task with `allow_timesheets=False` | Backend `UserError`; the note is still posted, the user sees a sticky error toast |
| User without an `hr.employee` record | Backend `UserError`; same handling as above |
| RPC failure after the note posts | Sticky danger notification surfaces the original error message; the note is preserved |

## Installation

```bash
docker compose exec odoo odoo -d <database> -i bf_chatter_timesheet --stop-after-init
```

After install, flush the asset cache:

```sql
DELETE FROM ir_attachment WHERE url LIKE '/web/assets/%';
```

then hard-refresh the browser.

## Usage

1. Open any task in a project that allows timesheets
2. In the chatter, switch to **Log note**
3. Type your note
4. Tick **Temps**, set the duration (manually or with a preset), click **Log**
5. The note is posted *and* a timesheet entry appears under the task's **Timesheets** tab — description = note body, duration = what you entered, employee = you, date = today

## License

LGPL-3

---

<sub>Authored and maintained by Blue Fox Inc. AI coding assistants were used as productivity tools during development.</sub>
