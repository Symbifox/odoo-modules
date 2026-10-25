# Fox Quest: declared absences and constance

Bridge between **Fox Quest** (`bf_gamification`) and **Contact absences** (`bf_contact_absence`). It
installs itself when both modules are present.

Fox Quest counts the streak (constance) in **working days**: an absence declared on the person's
contact form (or a closure of their employer that applies to all its staff) pauses the count without
breaking it.

With this bridge, an absence that changes **afterwards** recomputes the streak and the march towards
the constance day off of every person it touches:

- declared late (sick on Monday and Tuesday, declared on Wednesday): the streak that Wednesday's first
  action had broken comes back;
- corrected (dates, person, employer closure), archived, unarchived or deleted: the streak is
  recomputed on the new working days.

A time-off step already reached is never counted twice. The recount never breaks the action that
triggered it: an error is rolled back for the person concerned, and the absence is saved anyway.

Without this bridge, Fox Quest still reads absences to count working days, but recomputes the streak
only on the first action of the day and on the daily scheduled job.

## Requirements

| Dependency | Purpose |
|---|---|
| `bf_gamification` | Fox Quest: streaks, constance time off |
| `bf_contact_absence` | Absences declared on contacts |

Odoo 18 Community Edition.

## Changelog

### 18.0.1.0.0

- First version. Replaces the wrapper Fox Quest used to set at run time on the absence model
  (`base_automation` removed it in the process where an automated rule was edited); archiving an
  absence now recomputes the streak too.

## License

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-10-24, this version converts automatically to
  **LGPL-3.0-or-later**.
