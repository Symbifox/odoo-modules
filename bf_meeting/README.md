# Meetings (bf_meeting)

An Odoo 18 Community module covering the full life of a meeting: agenda,
calendar event, structured report, decisions, attendance, and two-way links
with tasks and knowledge matrices.

## Use case

Letting a project team plan, hold and document its meetings from inside Odoo,
with no external tool: preparing the agenda from open tasks, emailing it to
participants, taking structured notes, producing a branded PDF report, and
tracking decisions as knowledge matrix lines.

## Features

- **Agendas (`meeting.agenda`)** — title, date, project, participants, planned topics, email delivery to recipients
- **Reports (`meeting.record`)** — topics covered, decisions, structured JSON notes rendered as safe HTML, PDF report, send tracking
- **Decisions (`meeting.decision`)** — decision-makers, context, optional transfer into the knowledge matrices
- **Attendance (`meeting.attendance`)** — status (present / absent / excused) and role per participant
- **Tasks to discuss** — four ways of attaching a `project.task` to an upcoming meeting:
  - *Pinned*: an explicit link to one specific agenda
  - *Next client meeting*: appears on the client's next eligible agenda
  - *Next project meeting*: appears on the project's next eligible agenda
  - *All client/project meetings*: appears on every eligible agenda while the task stays open
- **Dynamic resolution** — tagged tasks are computed every time the agenda is opened (form, PDF, email) and disappear as soon as they are closed
- **Cancelling an agenda** — hard-linked tasks with no soft tag get a "To do" activity due today so they can be reassigned; tagged tasks roll over automatically to the next eligible agenda
- **Transfer to the report** — `action_create_meeting_record` moves hard-linked tasks into `meeting.record.task_ids` and clears the soft tag
- **Smart buttons** — next meeting on the task, reports and agendas on the project and on the calendar event, tasks to discuss on the agenda
- **Emails** — templates for sending the agenda and the report, with a dedicated section for tasks to discuss
- **PDF report** — branded rendering of the agenda with an "Action items to discuss" section
- **Unifying agenda ↔ report ↔ calendar event** — one `calendar.event` can carry both an agenda and a report; creating a report from an event that already has an agenda links the two automatically (`meeting.agenda.meeting_record_id`) and propagates the project
- **"Needs an agenda" flag** — on `calendar.event`, a computed `bf_needs_agenda` field (true when the meeting is upcoming, has no agenda and is not exempted); an alert banner on the form and a dedicated filter in the search view
- **Per-meeting opt-out** — a `bf_skip_agenda` checkbox on `calendar.event` for short or recurring internal meetings
- **Automatic pre-meeting reminder** — a daily `_cron_remind_unsent_agenda` cron creating a "To do" activity due today on the organiser (internal users only) when the meeting is within the next 7 days and the agenda has not been sent; idempotent through the activity's `summary`
- **Public contributions from recipients** — after an agenda in draft has been **sent** and **until it is confirmed**, the email includes a public tokenised link (`/meeting/agenda/<token>`) letting recipients (even without an Odoo account) **propose topics** and **leave comments/notes**. The window opens and closes automatically (`contributions_open ≡ sent AND draft state`); confirming closes the link. Proposed topics arrive in **moderation** (`source='contributed'`, `moderation_state='pending'`) and enter neither the PDF nor the email until a manager accepts them; comments are posted to the chatter and the organiser receives a review activity
- **Attachment visibility window** — an attachment on an agenda or a report can be visible only before, during (± 2 h) or after the meeting, or over a custom range (`bf_visibility_window`, `bf_visible_from`, `bf_visible_until`). Choosing a relative window computes the bounds from the linked meeting's date; an `ir.rule` filters reads for `group_meeting_user`, while `group_meeting_manager` always sees everything. Attachments on other models are untouched. ⚠️ **Known limitation**: treat this as a display convenience, not as access control. The rule compares against `time.strftime(...)`, but `ir.rule._compute_domain` is cached by `ormcache` on `(uid, su, model, mode, allowed_company_ids)`, with no time component: the timestamp is evaluated once and then frozen until the cache is invalidated, so an attachment can stay readable past its `bf_visible_until`. The exact call-time implementation exists (`ir_attachment._bf_visibility_domain`) but is not wired in yet
- **Dashboard** — an OWL view aggregating agendas and reports to follow up into KPI tiles and a 30-day completion rate, with per-user horizons (`bf_meeting_dashboard_lookahead_days` / `lookback_days`, capped at +90 / -180 days) and optional exclusion per contact (`bf_skip_dashboard`)

## Technical architecture

### Models

| Model | Role |
|---|---|
| `meeting.agenda` | Agenda (project, date, topics, tasks, recipients, state) |
| `meeting.agenda.topic` | A planned topic in an agenda (sequence, duration, presenter) |
| `meeting.record` | Structured report (project, date, JSON notes, PDF report) |
| `meeting.topic` | A topic covered in a report (key points, verbatim) |
| `meeting.decision` | A decision taken in a meeting (context, decision-maker) |
| `meeting.attendance` | A participant's attendance (status, role) |
| `project.task` (inherited) | Meeting attachment fields (`meeting_id`, `bf_meeting_agenda_id`, `bf_discuss_tag`, `bf_next_agenda_id`) |
| `project.project` (inherited) | "Reports" smart button |
| `calendar.event` (inherited) | "Reports" and "Agenda" smart buttons, `meeting_agenda_ids/id/count` fields, `bf_skip_agenda` (opt-out), `bf_needs_agenda` (computed), creation of an agenda or a report from the event |
| `project.knowledge.item` (inherited) | Many2many link to the reports referencing the item |
| `ir.attachment` (inherited) | Visibility window for meeting attachments (`bf_visibility_window`, `bf_visible_from`, `bf_visible_until`, `bf_is_visible_now`) |
| `res.company` (inherited) | `meeting_logo` — logo shown on the dark banner of PDFs and emails (falls back to the company's standard logo) |
| `res.partner` (inherited) | `bf_skip_dashboard` — excludes this contact's meetings from the dashboard |
| `res.users` (inherited) | Personal dashboard horizons (`bf_meeting_dashboard_lookahead_days`, `bf_meeting_dashboard_lookback_days`) |
| `meeting.dashboard` / `meeting.dashboard.line` | Meeting dashboard (OWL view aggregating agendas/reports to follow up) |

### Dependencies

| Module | Role |
|---|---|
| `project` | Projects, tasks, meeting attachment |
| `mail` | Chatter, activities, mail templates |
| `calendar` | Link with Odoo calendar events |
| `project_knowledge_matrix` | Knowledge matrices fed by decisions |
| `bf_onboarding_base` | Guided welcome panel (a configuration step) and the `report_brand_{primary,dark,logo}` brand fields on `res.company` (palette for PDF reports and emails) |
| `bf_timezone` | Displaying dates/times in the recipient's time zone |

The white-label module `bluefox_branding` is **not** required: it only exposes
and styles the `report_brand_*` fields, which have belonged to
`bf_onboarding_base` since its v18.0.2.0.0. Without it, reports and emails
render with the company's palette, or with Odoo's default colours
(`#714B67` / `#212529`) when none is configured.

### Security

- The `group_meeting_user` group — view and edit meetings of the projects the user has access to (through `project.message_partner_ids`)
- The `group_meeting_manager` group — full access to every report, agenda, decision and attendance record
- `ir.rule` rules on `meeting.record`, `meeting.agenda`, `meeting.topic`, `meeting.decision`, `meeting.agenda.topic`, `meeting.attendance` (one user/manager pair per model)
- `ir.rule` rules on `ir.attachment` — apply the visibility window to `meeting.record` / `meeting.agenda` attachments only, leaving others alone (see the known limitation above)
- `ir.rule` rules on `meeting.dashboard.line` — one global multi-company rule, plus the user/manager pair modelled on `meeting.record`. ⚠️ The SQL view aggregates **every** meeting in the database: `get_dashboard_data()` reads in raw SQL, outside the ORM, so neither the ACLs nor these rules apply there and it **reimplements the same guardrails by hand**. Any change to one must be mirrored in the other
- Standard ACLs declared in `security/ir.model.access.csv`

### Scheduled jobs

| Cron | Model | Frequency | Role |
|---|---|---|---|
| `ir_cron_remind_unsent_agenda` | `meeting.agenda` | daily | Creates a "To do" activity on the agenda for the organiser when the meeting is within 7 days and the agenda has not been sent |
| `cron_meeting_dashboard_daily_digest` | `meeting.dashboard` | daily | Daily meeting digest (legacy, shipped **disabled**; the `_cron_send_daily_digest()` method is kept for ad hoc triggering) |

### Safe HTML rendering

The structured JSON notes (topic title, points, open questions) are rendered to
HTML through `markupsafe.escape()` before concatenation, to prevent injection
when the content comes from an external source (AI transcription, user paste).

### Public contributions — security

The public controller (`controllers/main.py`, routes `type="http",
auth="public", csrf=False`) follows the `bf_sign` model:

- **Token = capability** — `secrets.token_urlsafe(32)` (256 bits), `copy=False`, `readonly`, `index=True`, restricted to the `group_meeting_user` group, so never serialised into a portal/public read. Minted on **send**, not on creation (minimal exposure surface).
- **No IDOR** — the URL carries only the token (no record `id`); resolution happens by token through `hmac.compare_digest` (constant time). A forged/expired token returns an indistinguishable `404`.
- **The window is re-checked server-side** — every GET and POST revalidates `contributions_open` after resolution: a tab left open cannot write after confirmation.
- **Sanitisation** — all free text goes through `markupsafe.escape` with strict caps (title ≤ 200, description/comment ≤ 4000, name ≤ 120, email ≤ 254). Topic creation uses an explicit dictionary (`source`/`moderation_state` cannot be driven from the POST).
- **Rate limiting** — two per-IP limiters: token failures (10 / 300 s) and POST volume (5 / 60 s). The IP used is **the socket peer's**, never `X-Real-IP` / `X-Forwarded-For`: those headers are forgeable if the endpoint is directly reachable, and reading them ourselves would make the limiter bypassable. Under `proxy_mode = True`, werkzeug (ProxyFix) has already rewritten `remote_addr` from a trusted hop count.
- **Read allowlist** — the public page receives only the title, the formatted date, the objectives (text) and the **names of accepted topics**. No context, preparation, notes, tasks, attachments, participants, chatter, or another contributor's proposal.
- **Writes under `sudo()`** — the public user has no ORM rights; every write is explicit with safe dictionaries. Notes are posted with `author_id=False` (the contributor's identity lives in the body, never forged into a `res.partner`).

## Installation

```bash
docker compose exec odoo odoo -d <database> -i bf_meeting --stop-after-init --no-http
```

After installation, the "Manager" group is assigned by default to
`base.user_admin`; other users receive the "User" group through the profile
settings.

## Licence

LGPL-3

## Acknowledgements

Created and maintained by Les services de consultation Blue Fox, Inc. AI coding assistants were used as
productivity tools during development.
