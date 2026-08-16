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

## Installation

```bash
# Install / update through your usual Odoo deployment procedure, e.g.:
odoo -d <database> -i bf_stepbystep_clients --stop-after-init
```

Odoo dependencies: `base`, `project`, `hr_timesheet`.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-08-12, this version converts automatically to
  **LGPL-3.0-or-later**.
