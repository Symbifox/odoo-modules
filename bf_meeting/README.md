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
- **Brand colours taken from the company on the document** (v18.0.3.56.0): the agenda and report PDF templates read `doc.company_id.report_brand_primary` / `report_brand_dark`, as the logo and footer already did. They used to read `env.company`, that is the ACTIVE company of whoever triggers the printing: on a multi-company database, a meeting belonging to a secondary company came out with its own logo but in the main company's colours as soon as the main company sat first in `allowed_company_ids`, which is the case whenever both companies are ticked, and in any printing triggered by an email or a cron. Three CSS rules of the agenda additionally carried a hard-coded blue that no company colour could displace. ⚠️ `env.company` is the **first** of `allowed_company_ids`, not a member: `action_send_report_direct` built that list from a `set()`, which put the main company back in front and silently undid its own `with_company()`
- **The logo survives the trip to the reader** (v18.0.3.57.0): the agenda email, the report email and the public contribution page go through a shared route, `/brand/logo/<company>[/<variant>]` (provided by `bf_onboarding_base`), instead of `/web/image/res.company/...`. The latter only serves the real image for companies the anonymous user may read, that is the single company of the website; for any other one Odoo swallows the AccessError and returns its grey placeholder **with an HTTP 200**. A secondary company's logo therefore vanished from emails with no error code to show for it, while the PDF, rendered server-side with an internal user's rights, stayed correct. The route reads in sudo, as Odoo's own `/logo.png?company=` already does, and resizes to 120 px high (the source field is an `image_1920` that can weigh over a megabyte). ⚠️ A **variant** (`meeting`, `brand`) travels in the URL, never a field name: without that allowlist the route would become an arbitrary sudo read of `res.company`
- **Exchange between tenants** — the report email can carry a machine-readable
  copy (a `.json` file) that another Symbifox tenant imports into its own
  Meetings app without retyping. Off by default, per company and per report
- **Unifying agenda ↔ report ↔ calendar event** — one `calendar.event` can carry both an agenda and a report; creating a report from an event that already has an agenda links the two automatically (`meeting.agenda.meeting_record_id`) and propagates the project
- **"Needs an agenda" flag** — on `calendar.event`, a computed `bf_needs_agenda` field (true when the meeting is upcoming, has no agenda and is not exempted); an alert banner on the form and a dedicated filter in the search view
- **Per-meeting opt-out** — a `bf_skip_agenda` checkbox on `calendar.event` for short or recurring internal meetings
- **Opt-out by calendar tag** — the same two exemptions (`bf_skip_agenda`, `bf_skip_dashboard`) also sit on `calendar.event.type`, so a whole category is waived once instead of meeting by meeting. Follow-up works by exception — a meeting is presumed to deserve an agenda and a report — and on a real calendar the exceptions are a routine: weekly stand-ups, focus blocks, reminders. The tag's value is **copied onto the meeting**, not computed from it, so an individual meeting stays editable independently of its tag afterwards
- **Meeting preparation on the calendar event** — the agenda and report links, the two opt-outs and the two responsible-person fields sit in the event's own notebook page, each with its label. Whether an event already has an agenda and a report is readable without opening it: two badges ride **inside** the tile's title, ahead of its text, so they take width from a title that truncates rather than a line of their own — on a half-hour tile a badge row of its own pushed the title out of sight entirely
- **Automatic pre-meeting reminder** — a daily `_cron_remind_unsent_agenda` cron creating a "To do" activity due today on the organiser (internal users only) when the meeting is within the next 7 days and the agenda has not been sent; idempotent through the activity's `summary`
- **Agenda send indicator** — stored computed `send_state` field (not sent / send unconfirmed / sent / sent by hand), rendered as a banner on the form and a badge in the list, and available as a filter and a group-by. `sent_date` only records that a send was **started** — it is stamped as soon as the composer opens, because the public contribution block derives from it; `email_sent_date` records the **actual departure**, stamped after `send_mail()` or by `_message_post_after_hook` when the composer posts a message to explicit recipients. A composer opened then closed therefore reads "send unconfirmed" instead of counting as sent, and `email_sent_date` is what the reminder cron and the dashboard now read
- **Send declared by hand** — a "Mark as sent by hand" button on the agenda (`sent_manually`) and on the meeting report (`report_sent_manually`, which forces `report_state` to sent) for transmissions made outside Odoo: pasted into a mail client, dropped in a chat thread, handed over in person. The action is logged in the chatter with its author, and is reversible
- **What changed since the last send** (v18.0.3.55.0) : when an agenda actually departs, the module freezes a **baseline** of its communicated surface (`sent_snapshot_json`, `sent_snapshot_date`), built from `_get_report_data()`, the very source the PDF attached to the email consumes. The form then shows a banner detailing what has moved since (topic added, removed, renamed, duration or presenter changed, context changed, order changed, date, location, duration, participants, action items added or removed), a **per-topic badge** (`change_since_sent`) in the topic list, and a "Changed since send" filter.
  - The baseline is taken on **all three** real-send paths (button, composer, manual declaration) and is retaken on **every** departure: recipients hold the last copy they received. It is not taken when the composer merely opens, where only `sent_date` moves.
  - It compares **content, not timestamps**, and that choice is measured. On a real fleet of 53 agendas that actually went out before their meeting, a "the record was written after the send" flag lights up on 51 of them, and "a topic row was written after the send" on 18, while only 11 saw their communicated content change. One gesture explains the gap: `action_start_meeting` pre-fills `live_notes_html` on **every** topic, touching 66 rows without changing a word of what recipients received. A check that fires 51 times out of 53 checks nothing.
  - The baseline carries **no body text**: objectives, context, preparation and each topic description travel as a truncated `sha256` digest. It can say that a context changed, never what it said. Topic **names** travel in clear, because without them no removal can be named.
  - The **action items** section falls silent as soon as `agenda_task_ids` resolves nothing (agenda not active, or date passed). That list empties itself, and comparing it to the baseline would announce their wholesale removal at the minute the meeting starts. The `_carries_agenda_tasks()` predicate is shared by the task resolution and by the diff so the two cannot drift apart.
  - An agenda that went out **before** this version has no baseline, and the send banner says so (`sent_baseline_missing`), so that showing no changes is not read as "nothing changed".
  - **Optional block in the resend email** (`resend_include_changes` on the agenda, `meeting_resend_changes_default` on the company, both **unchecked by default**) : a re-sent agenda can carry a "what changed since the last send" panel. It is rendered **before** the new baseline is taken, so it compares against the copy recipients actually hold. It stays silent on a first send, where there is nothing to compare.

- **Public contributions from recipients** — after an agenda in draft has been **sent** and **until it is confirmed**, the email includes a public tokenised link (`/meeting/agenda/<token>`) letting recipients (even without an Odoo account) **propose topics** and **leave comments/notes**. The window opens and closes automatically (`contributions_open ≡ sent AND draft state`); confirming closes the link. Proposed topics arrive in **moderation** (`source='contributed'`, `moderation_state='pending'`) and enter neither the PDF nor the email until a manager accepts them; comments are posted to the chatter and the organiser receives a review activity
- **Attachment visibility window** — an attachment on an agenda or a report can be visible only before, during (± 2 h) or after the meeting, or over a custom range (`bf_visibility_window`, `bf_visible_from`, `bf_visible_until`). Choosing a relative window computes the bounds from the linked meeting's date; the window filters access for `group_meeting_user`, while `group_meeting_manager` always sees everything. Attachments on other models, and any call made under `sudo()`, are untouched. Since **v18.0.3.45.0** the window is enforced from Python (`ir_attachment._bf_visibility_domain`, wired into `_search` and `_check_access`) rather than by an `ir.rule`: an `ir.rule` domain is cached by `ormcache` on `(uid, su, model, mode, allowed_company_ids)`, with no time component, so a timestamp written into `domain_force` was evaluated once and then frozen until the cache was invalidated — an attachment stayed readable past its `bf_visible_until`. `_search` covers searches, list views **and** direct URL reads (`/web/image`, `/web/content`), because `fetch()` routes reads through `_search`; `_check_access` covers writes, unlinks and the `has_access` / `_filtered_access` callers
- **Assistant pass indicator** — the optional assistant pass (agenda pre-fill, report refinement) writes its progress on the record itself: `refine_state` (not started / running / done / error), `refine_date` and `refine_message`, surfaced as a banner on both forms. `refine_in_progress` only stays true inside the window where the pass can still return (`bf_meeting.refine_stale_minutes`, 20 by default), so a pass killed mid-flight stops hiding the launch button and is reported as "no news" instead of silently looking busy forever
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
| `bf.meeting.document.mixin` | Shared **Documents** tab: computed One2many over `ir.attachment` (`res_model`/`res_id`), with the inverse that materialises new lines and deletes removed ones |
| `meeting.dashboard` | RPC entry point for the OWL dashboard — an `AbstractModel`: no field, no table, no SQL view, methods only |
| `meeting.dashboard.line` | SQL view aggregating the agendas and reports to follow up; the source of every dashboard row |
| `meeting.exchange` | Builds, validates and applies the portable copy of a report (`AbstractModel`: no field, no table) |
| `meeting.exchange.import.wizard` | Upload, check and import a report exported by another tenant (manager group only) |

### Dependencies

| Module | Role |
|---|---|
| `project` | Projects, tasks, meeting attachment |
| `mail` | Chatter, activities, mail templates |
| `calendar` | Link with Odoo calendar events |
| `project_knowledge_matrix` | Knowledge matrices fed by decisions |
| `bf_onboarding_base` | Guided welcome panel (a configuration step) and the `report_brand_{primary,dark,logo}` brand fields on `res.company` (palette for PDF reports and emails) |
| `bf_timezone` | Displaying dates/times in the recipient's time zone |
| `bf_ai_bridge` | The single Unix-socket transport used to reach the assistant; the module used to carry two copies of that helper inline |

The white-label module `bluefox_branding` is **not** required: it only exposes
and styles the `report_brand_*` fields, which have belonged to
`bf_onboarding_base` since its v18.0.2.0.0. Without it, reports and emails
render with the company's palette, or with Odoo's default colours
(`#714B67` / `#212529`) when none is configured.

### Security

- The `group_meeting_user` group — view and edit meetings of the projects the user has access to (through `project.message_partner_ids`)
- The `group_meeting_manager` group — full access to every report, agenda, decision and attendance record
- `ir.rule` rules on `meeting.record`, `meeting.agenda`, `meeting.topic`, `meeting.decision`, `meeting.agenda.topic`, `meeting.attendance` (one user/manager pair per model)
- **No `ir.rule` on `ir.attachment`** — the visibility window is enforced from Python (`ir_attachment._search` / `_check_access`) precisely because an `ir.rule` domain is frozen by `ormcache` and cannot depend on the current time (see above). The restriction targets only non-manager members of `group_meeting_user`, only on `meeting.record` / `meeting.agenda` attachments, and lifts entirely under `sudo()`
- `ir.rule` rules on `meeting.dashboard.line` — one global multi-company rule, plus the user/manager pair modelled on `meeting.record`. ⚠️ The SQL view aggregates **every** meeting in the database: `get_dashboard_data()` reads in raw SQL, outside the ORM, so neither the ACLs nor these rules apply there and it **reimplements the same guardrails by hand**. Any change to one must be mirrored in the other
- No ACL on `meeting.dashboard`: the model is abstract (no table), so `ir.model.access` is never consulted for it. Its RPC methods carry their own scoping, in SQL (see the previous bullet)
- Standard ACLs declared in `security/ir.model.access.csv`

### Scheduled jobs

| Cron | Model | Frequency | Role |
|---|---|---|---|
| `ir_cron_remind_unsent_agenda` | `meeting.agenda` | daily | Creates a "To do" activity on the agenda for the organiser when the meeting is within 7 days and the agenda has not been sent |
| `cron_meeting_dashboard_daily_digest` | `meeting.dashboard` | daily | Daily meeting digest (legacy, shipped **disabled**; the `_cron_send_daily_digest()` method is kept for ad hoc triggering). Its buckets come from `_get_digest_buckets()`, which `daily_todo_digest` also consumes: the "agendas to prepare" section **excludes agendas already sent** — an agenda that left but stayed in draft has nothing left to prepare, and its confirmation shows on the dashboard |

### Safe HTML rendering

The structured JSON notes (topic title, points, open questions) are rendered to
HTML through `markupsafe.escape()` before concatenation, to prevent injection
when the content comes from an external source (AI transcription, user paste).

### Exchange between tenants

Two Symbifox tenants that meet each other exchange an email and a PDF: readable,
but not reusable. The report email can therefore carry a machine-readable copy of
itself, and the receiving tenant imports it in one step. Three rules hold the
whole design:

- **The payload carries only what the PDF already shows.** The exchange is not a
  new disclosure, it is the same content in another shape. The raw transcript,
  the review notes, the refine state and the attachments stay with the sender.
- **The payload carries no markup at all.** Topic bullets are reduced to text
  lines on export and re-rendered on import through the module's own escaped
  template, so no tag coming from a file ever reaches the importer's browser.
  This removes the whole question of sanitising imported HTML.
- **The payload carries no reusable identifier.** The two databases are separate
  and their ids overlap; people travel as a name and an email address.

The importer matches **existing** contacts only, by normalised email: it never
creates a contact from a file received by email, and anyone it cannot match is
named in a "Received copy" panel on the report, so nothing is lost and nothing is
invented. Action items are imported as text, not as tasks. The copy lands in
**draft**, with no recipient and no sent date, because the portal opens a report
to a client as soon as it is marked sent — a copy that inherited the sender's
state would surface in the importer's own client portal.

The file is untrusted input: it is refused above 512 kB (a real report weighs
around 5 kB), refused outright on a structural mismatch rather than repaired
silently, capped on every list and string, and the import itself is restricted to
the manager group.

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

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-08-12, this version converts automatically to
  **LGPL-3.0-or-later**.

## Acknowledgements

Created and maintained by Les services de consultation Blue Fox, Inc. AI coding assistants were used as
productivity tools during development.
