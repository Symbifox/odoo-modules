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
  budget consumed and progress through the schedule.
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

## Client portal (v18.0.2.4.0)

On a project form, the **Client portal** tab publishes the page and copies its
address (`/progression/<project>/<token>`), ready to paste in an email. Only
members of *Project: Administrator* can publish, unpublish, copy the address or
issue a new one; the check is on the server, not only in the view.

What the client sees: the name of the project and of the client, the stages
ticked **Visible on client portal** (grouped by step number), whether each one
is done, in progress or upcoming, the progress of the current step and its
**client instruction**. The step in progress is the first one that still has an
open task. No task name, assignee, hour or internal stage is shown.

The page is shown in the language of the project's customer when that language
is installed. Every refusal (unknown or archived project, wrong token, unpublished
portal) answers 404, so a visitor cannot tell them apart. The page sends
`Referrer-Policy: no-referrer` and `X-Robots-Tag: noindex`.

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
that add content to a step. The controller's `_progression_mode_de_depot()`
returns `"direct"`; a bridge module can switch it, for instance to upload through
`bf_securetransfer` storage.

## Installation

```bash
# Install / update through your usual Odoo deployment procedure, e.g.:
odoo -d <database> -i bf_stepbystep_clients --stop-after-init
```

Odoo dependencies: `base`, `project`, `hr_timesheet`, `portal`.

## Languages (v18.0.2.3.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English. The dashboard reads stage, project and activity type names in the user's language first, and formats dates in it. Its short labels (mandate start and end, days ahead) have terms of their own: the web client merges the JavaScript translations of every module, and a bare "Start" or "in" would take another module's French.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-10-16, this version converts automatically to
  **LGPL-3.0-or-later**.
