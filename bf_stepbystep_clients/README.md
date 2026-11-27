# bf_stepbystep_clients — Client engagement tracking (Step-by-Step)

An Odoo 18 (CE) module that visualises the **linear progress** of any client
engagement. It turns a project's stages (`project.task.type`) into a
step-by-step progress bar, and provides an internal dashboard showing at a
glance where each client stands (current step, hours budget consumed, schedule
progress).

The module is **domain-agnostic**: it works for any sequential engagement
(implementation, compliance, onboarding, and so on). The steps are defined by
your own project stages.

## Features

- **OWL dashboard** (`bf.stepbystep.dashboard`) listing every active engagement
  with, for each one: the current step, progress as a percentage, the hours
  consumed against the project's allocated hours, and progress through the
  schedule.
- **Per-client detail view**: the full sequence of steps, the step reached, the
  tasks and the hours.
- **Per-stage configurable progress** through four fields added to
  `project.task.type` (see below): step number, short label, client visibility
  and client instruction.
- **Automatic sector classification** of the engagement from the project name
  (childcare centre, non-profit, school, business, and so on) for grouping in
  the display.
- **Client portal**: a public page, opened by an address with its own token,
  where the client sees the steps of the engagement, the step in progress and
  its client instruction. No account needed. See *Client portal* below.
- **Documents and links per step**: a stage can ask the client for documents to
  deposit on the page, and offer links (a booking page, a questionnaire, a
  video) shown as buttons. See *Links on a step* below.
- **An idempotent post-install hook** that prefills step numbers and labels on
  existing stages from a keyword table (safe to re-run).

## Fields added to `project.task.type`

| Field | Type | Description |
|-------|------|-------------|
| `progression_step_number` | Integer | Rank of the step in the linear progression. `0` (default) = stage excluded from the visualisation. |
| `progression_step_name` | Char (translated) | Short label displayed (e.g. "Kick-off"). If empty, the stage name is used. |
| `progression_client_visible` | Boolean | Step can be shown to the client (untick it for internal stages: review, billing, and so on). |
| `progression_client_action_hint` | Text (translated) | Instruction to present to the client when the engagement reaches this step. |

## Configuration

1. On each project stage (`Project › Configuration › Stages`), fill in the
   **progression step number** (1, 2, 3…) and a **short label**. Leave stages to
   exclude at `0`.
2. Untick **Visible in client portal** for purely internal steps.
3. Open the **Engagement tracking** menu to see the dashboard.

On install, the post-install hook attempts to map existing stages
automatically; adjust as needed.

## Client portal (v18.0.2.5.0)

On a project form, the **Client portal** tab publishes the page and copies its
address (`/progression/<project>/<token>`), ready to paste in an email. Only
members of *Project: Administrator* can publish, unpublish, copy the address or
issue a new one; the check is on the server, not only in the view.

What the client sees: a neutral title (**Progress of your mandate**), the name
of the client the mandate is for, the stages ticked **Visible on client
portal** (grouped by step number), whether each one is done, in progress or
upcoming, the progress of the current step and its **client instruction**. The
project's own name is **never** shown: it serves internal tracking and often
carries a site shorthand or the tier of the offer that was sold. The step in
progress is the first one that still has an open task. No task name, assignee,
hour or internal stage is shown.

The page names the project's customer, and is shown in that customer's
language when it is installed. A bridge module can make both follow the **beneficiary** of the mandate
instead, where someone else orders and pays for it: the page takes the
party it names from `portal_client`, a value a bridge may replace. Every refusal (unknown or archived project, wrong
token, unpublished portal) answers 404, so a visitor cannot tell them apart.
The page sends `Referrer-Policy: no-referrer` and `X-Robots-Tag: noindex`.

The portal has **its own token**, not the project's `access_token`: that one
also opens Odoo's standard `/my/projects/<id>` routes, task descriptions
included. *New address* replaces the token and closes the old address.

### Documents the client deposits

On a stage form, **Documents the client deposits on the portal** lists the
documents expected at that stage (name, instructions to the client, required or
optional). A stage serves several projects: the list applies to every engagement
that goes through it.

On the portal, the documents of the completed and current steps are listed with
a file field; those of upcoming steps are not shown. The instructions of a
document stay shown at a completed step, since the document can still be
deposited there; only the client instruction of the STEP is limited to the step
in progress. Archived documents are neither listed nor accepted.

A deposited file becomes at once an **attachment of the stage's task** (the first
open task of the project in that stage; when every task of the stage is closed, or
there is none, the project itself), with an internal note logged on its chatter in
the client's language. The note does not make the client a follower and nothing is
emailed. Each deposit is kept in `bf.progression.deposit` (document, file name,
size, SHA-256, date, IP address) and listed on the project's **Client portal** tab.
A new version is added; it never replaces the previous one. Deposits are read like
the project that holds them: a project user does not see those of a private project
they do not follow, nor those of another company.

Accepted files: PDF, images (JPEG, PNG, HEIC, WebP, TIFF) and office documents
(Word, Excel, OpenDocument, RTF, text, CSV). Limits, as system parameters:
`bf_stepbystep_clients.portal_max_upload_mb` (default 25) and
`bf_stepbystep_clients.portal_deposits_per_hour` per project (default 30). The
file name loses any path, control character and Unicode format character (such as
the right-to-left override). The form posts plain HTML, with no JavaScript and
without the `capture` attribute, so a phone can send a photo or a PDF it already
has.

Each step on the page carries an empty `<div name="step_extra"/>` for modules
that add content to a step, and the page itself carries a `<div name="page_extra"/>`
right under its heading for modules that add a block to the page. The controller's `_progression_mode_de_depot()`
returns `"direct"`; a bridge module can switch it, for instance to upload through
`bf_securetransfer` storage.

## Links on a step (v18.0.2.9.0)

What a stage asks of the client is no longer only documents. In the stage form,
the list *What the client finds on the portal* takes two types of items:

- **Document to deposit**: what the portal already did. Existing items stay
  documents; nothing is moved.
- **Link**: a label, an `https://` address and an optional instruction. The
  client page shows it as a button, opened in a new tab, at a stage that is done
  or in progress, like documents. Use it for an appointment booking page, a
  public questionnaire, a video or a shared folder.

Configure it once on a stage and every mandate that goes through that stage gets
it. A link is never a document: it has no *To send* badge, nothing can be
deposited on it (the direct deposit and the start of an S3 upload both refuse it),
and it does not count as a required document. A document that has already
received deposits, or has an S3 upload under way, cannot be turned into a link:
add a new link instead. Links are shown in their own block, after the documents
of the stage; the order set in the list applies within each block. Only
`https://` addresses are accepted, without spaces; the page checks the address
again before showing the button, and the label and instruction are escaped like
the rest of the page.

## A code by email, once per device (v18.0.2.7.0)

Tick **Ask a code by email** on the project's *Client portal* tab and the page
stops opening on the link alone: the client receives a six-digit code at the
mandate's email address, types it once, and the device is remembered for the
length of the mandate. No account, no password — the code proves that whoever
holds the link also reads the email the link was sent to.

| What | Where it is kept | Default |
|------|------------------|---------|
| The code | fingerprint only (`bf.progression.otp`), HMAC-SHA256 salted per mandate with the database secret | 30 minutes, 5 attempts |
| Attempts | counted per mandate, per day, on top of the per-code limit | 20 |
| How many codes | per mandate, per hour, failures included | 5 |
| The device | fingerprint only (`bf.progression.device`), the token itself lives in a cookie | 90 days |

The five limits are `ir.config_parameter` values:
`bf_stepbystep_clients.portal_otp_minutes`, `portal_otp_attempts`,
`portal_otp_attempts_per_day`, `portal_otp_per_hour` and `portal_device_days`.

🔴 **The guard lives in `_progression_projet`**, which every public path of the
module calls — the page, the document deposit, and the routes of bridge modules
that know nothing about codes. Only the page itself passes `exiger_otp=False`,
because it has to *ask* for the code instead of refusing. A new public route
written without a thought is therefore protected by default, and that is the
point: the protection of the page would be worth nothing if the deposit route,
which writes, stayed open.

**The code goes to the party the page names, and never to anyone else.** Under
an ordering customer the page names the beneficiary: if that company has no
email address of its own, the page says so rather than falling back on the payer
— who would otherwise receive the code to a file that is not theirs, while the
reader holding the link stayed out for good.

⚠️ **The feature depends on the mail queue being alive.** `_trigger()` on a
disabled cron is a no-op, and Odoo disables a cron that has crashed — four mail
crons were found switched off on one tenant after a relay started rejecting. So
the module refuses to mint a code while *Email: Email Queue Manager* is
inactive, and says *the code could not be sent* instead of promising one that
will never arrive. A message still `outgoing` five minutes after being queued is
read the same way. If an OTP-protected page starts telling clients that, look at
that cron first.

**The email goes through the queue, triggered at once** (`_trigger()` on the
mail scheduler), not through a synchronous send. A public route that waits on
SMTP holds an HTTP worker for the whole exchange: on a tenant with two workers,
two page loads during a slow relay are enough to take everyone down, internal
users included. The failure is therefore noticed on the **next** page load, from
the message's state, and the page says so then. A failed code is not deleted —
it stays and still counts against the hourly cap, so an outage cannot reopen the
burst.

**Exhausted attempts do not mint a new code.** Whoever holds the link could
otherwise burn five attempts, reload, and start again with five fresh ones; the
real limit was 25 attempts an hour, and the "reload for a new one" message told
them how. A code whose attempts are spent answers *too many codes* until it
leaves its window, and the mandate also has a daily attempt budget.

The closed page shows nothing of the mandate: no client name, no stage, no
instruction, no expected document. The address the code went to is masked
(`c***@domain.com`) — enough for the client to recognise their own inbox, not
enough to teach an address to whoever found the link.

The code travels in clear only in the email itself, which is `auto_delete`: Odoo
removes the message as soon as it is out, and with it its `mail.message`, so a
live code does not sit in the database waiting to be read. The fingerprint is a
salted HMAC, but the salt is the database secret, which lives in the same dump:
against someone holding a full dump, six digits are a short search. What the
fingerprint buys is that a *read* of the table hands out nothing usable; the
short life of the code is what buys the rest. The email carries no
`model`/`res_id`, so it posts nothing to the project's chatter.

The cookie is `HttpOnly`, `SameSite=Lax` and its path is scoped to the mandate:
the device of one file does not travel to another. Its `Secure` flag follows
`request.httprequest.is_secure` rather than being hard-coded, so the bench and
the test suite can run over plain HTTP; in production every tenant runs behind
`proxy_mode = True` and the flag is set.

**Forget the devices and codes** (button, *Project: Administrator* only) is there
for the two cases that happen: someone leaves the client's team although their
device stays recognised for 90 days, and someone holding the link burns the
attempts and the hour's codes, leaving the real client locked out.

## The mandate manager is notified (v18.0.2.8.0)

When the client acts on the page, the project manager of the mandate (the
project's **Project Manager**) is notified. Three actions count:

- a document deposited (direct upload, or the S3 bridge for large files);
- the questionnaire completed from the page, or closed by its time limit (with
  `bf_stepbystep_survey` 1.1.0);
- wrong access codes entered several times in a row: 3 by default, then again
  at 6, 9 and so on. A right code, "Forget the devices and codes" or "New
  address" starts the count again, and so does a day without error. The
  threshold is the system parameter `bf_stepbystep_clients.portal_otp_alert_after`.

**Progress > Configuration > Settings** chooses the channels, per company (the
mandate's, or the manager's when the project has none). They add up:

| Channel | Default | What arrives |
|---|---|---|
| Activity to do | on | An activity due today on the record that received the action (the task of the stage for a document, the project otherwise). Ten files for the same document make one activity, with one line each, dated at the client's action. |
| Odoo notification | off | A notification to the manager only. It follows the user's preference: a user set to "by e-mail" gets it by e-mail — and then not a second time when the e-mail channel is on too. |
| E-mail | on | An e-mail to the manager's address, whatever their preference. |

How it is sent: the client's action only **queues** a notice
(`bf.progression.notice`), and the scheduled action "Progression: send the
notices of client actions" sends it at once, in its own transaction. Nothing the
notice does can make the client's upload fail or be replayed. A notice whose
channels all fail is tried three times, five minutes apart, then left as
*Failed*; a channel that failed or had nothing to send (a manager without an
e-mail address) is written on the notice. **Progress > Configuration > Client
action notices** lists them, failed ones in red, with a *Retry* button (administrators only: a project manager reads the notices, and cannot put them back in the queue). Sent notices are deleted after 30
days, failed ones after 90.

⚠️ That scheduled action is installed **active**, and every module update
switches it back on: tenants where scheduled actions are switched off on
purpose (a demo, a clone) must switch it off again after the update. If it is
off, notices wait in the queue and the log says so.

What the notice never does:

- **No notice carries the document or quotes what the client wrote**, not even
  the file name: an activity marked done is copied to the record's thread, from
  where it can reach followers by e-mail. Notices name the expected document
  and the deposit number, and link to the record; the file itself is attached
  to the task under its own name.
- The visitor's IP address is not copied into any notice; the deposit and the
  access code keep it, as before.
- The client is never subscribed to anything or made a recipient. Odoo
  subscribes the manager to the record that carries the activity, as it does
  for any assigned activity.

A mandate without a manager notifies nobody; the action is logged.

## What the mandate writes on the page (v18.0.2.6.0)

Two texts the mandate carries, on the **Client portal** tab of the project:

| Field | Description |
|-------|-------------|
| `progression_program_label` | Public name of the program this mandate belongs to ("Export Acceleration"). Shown to the client so they can tell one mandate from another. |
| `progression_program_label_position` | `overline` (above the heading, discreet) or `inline` (in the heading line, impossible to miss). |
| `progression_confidentiality_note` | Confidentiality notice for mandates where it is contractual. |

Both are plain text, rendered **escaped**: a note pasted from an email does not
carry its HTML onto a public page. Neither is translatable — the program name is
a proper noun, and a contractual notice must not serve one language a version
that a correction in the other language never reached. A project copy keeps both
(a second mandate of the same program starts from the same page); what opens
access, the portal token, does not.

The inline separator is a CSS `::before`, not a text node: an inline `<span>`
would enter the translation catalog as a msgid carrying its own markup.

## Client mandates only (v18.0.2.8.1)

The dashboard lists only the projects that have a configured progression: at
least one stage with a progression step number and visible on the client
portal. Internal projects, hour banks, templates and personal projects have no
such stage and no longer appear in the cards or the counters. To bring a
mandate onto the dashboard, number its stages (see *Configuration*).

## Readable notice list (v18.0.2.8.3)

**Progress > Configuration > Client action notices** shows what happened and how
the manager was told, in the reader's language: the *Event* column reads
"Document deposited", "Questionnaire completed" or "Wrong access codes", and
*Sent by* reads "Activity, email", instead of the internal codes (`deposit`,
`activity,email`). The codes stay on the record; a bridge module that adds its
own event shows its code until it names it.

## Mandates without a budget (v18.0.2.4.3)

The budget of a mandate is the project's allocated hours. A project without
allocated hours now reads "No budget": its card and detail show the hours
consumed, with no percentage, no budget bar and no budget alert, and it is left
out of the Over budget, Critical and Warning counters. The budget filter offers
"No budget" to list these mandates. Earlier versions assumed 40 hours, which put
every unbudgeted mandate over budget.

The same version reserves the dashboard to project users
(`project.group_project_user`). Its data method reads projects, clients and
hours in raw SQL; any other user calling it over RPC, a portal user included,
now gets an access error instead of every mandate.

## Private projects (v18.0.2.4.4)

The dashboard lists only the projects the user can read in Project: a private
project (visible to its followers) that the user does not follow is left out of
the cards and the counters. The mandate detail, like the list, is reserved to
project users; a portal client who follows a shared project can read the project
itself but not its dashboard detail (tasks, hours, internal stages).
The **Progress** menu itself now shows only to project users.

## Timesheets in the detail (v18.0.2.4.5)

The recent activity of a mandate detail lists only the timesheet entries the user
may read under the Timesheets access rights: a user limited to their own
timesheets sees their own entries, not their colleagues'. The hours consumed
against the budget remain the project total for everyone.
In the same way, the overdue and upcoming tasks, the step counts and the chatter
messages of the detail cover only the tasks the user can read (company rules
included).

## Installation

```bash
# Install / update through your usual Odoo deployment procedure, e.g.:
odoo -d <database> -i bf_stepbystep_clients --stop-after-init
```

Odoo dependencies: `base`, `project`, `hr_timesheet`, `portal`, `mail`.

## Languages (v18.0.2.3.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English. The dashboard reads stage, project and activity type names in the user's language first, and formats dates in it. Its short labels (mandate start and end, days ahead) have terms of their own: the web client merges the JavaScript translations of every module, and a bare "Start" or "in" would take another module's French.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-11-27, this version converts automatically to
  **LGPL-3.0-or-later**.
