# Symbifox — Recurrence anchoring (`bf_recurrence`)

One field, per recurring series, that decides where the next deadline is measured
from: the old deadline (core Odoo) or the **actual completion date**.

## The problem

Core `project` knows a single anchor. In
`project.task.recurrence._create_next_occurrence_values()`, every field to be
postponed — there is exactly one, `date_deadline` — goes through
`value and value + self._get_recurrence_delta()`. The anchor is therefore always
the old deadline, never the moment the task was actually closed.

Three consequences:

1. **Finishing early buys you nothing.** A weekly task wrapped up five days ahead
   still comes back on the originally planned date.
2. **Falling behind is never absorbed.** On a daily series left alone for three
   weeks, every completion recreates an occurrence that is already overdue at
   birth.
3. **A series with no deadline dies.** `value and value + delta` returns `False`,
   so the next occurrence is created without a deadline and never reschedules
   again.

## What this module adds

A **Compute the next deadline** field on `project.task.recurrence`, mirrored onto
the task like the other recurrence fields:

| Value | Effect |
|---|---|
| **From the deadline** (default) | Core behaviour, unchanged. |
| **From the completion date** | The next deadline starts from the moment of closing, plus the interval. |

The choice is made once per series, at creation or later, instead of coming back
at every completion.

## Where it hooks in

| Hook | Role |
|---|---|
| `project.task._get_recurrence_fields()` | A single addition covers recurrence creation in `create()`, its update in `write()`, and the `_compute_repeat()` mirror. |
| `project.task.recurrence._get_recurrence_delta()` | Returns the anchoring shift when one has been computed for the current pass. |
| `project.task.recurrence._create_next_occurrence()` | Computes that shift and puts the `repeat_until` guard on the right anchor. |
| `project.task.recurrence._create_next_occurrence_values()` | Fills in the deadline of a series that had none. |

The shift travels through `_get_recurrence_delta()` rather than through a
post-hoc rewrite of the deadline, and that is deliberate: core applies this delta
to the occurrence **and**, recursively, to each of its subtasks, then reuses it
for the `repeat_until` guard. One entry point is therefore enough to keep the
tree coherent and the end bound correct.

## Details that matter

**The completion anchor is `date_end`, falling back to the current instant.**
`date_end` is only set when the write also changes stage and the target stage is
folded (`project.task.update_date_end()`). Closing through state alone — the
checkbox in a list, the kanban selector — leaves it empty. The fallback is exact:
`_inverse_state()` runs inside the very write that closes the task, so "now" **is**
the moment of completion.

**`date_last_stage_update` cannot serve as an anchor.** It is set *after*
`super().write()`, while `_inverse_state()` runs *during* it. Reading it there
would yield the date of the previous stage change.

**Subtasks keep their offsets.** Since the shift is uniform, a subtask due two
days after its parent stays due two days after it in the next occurrence.

**"Until" series stop at the right time.** The core guard compares
`old deadline + interval` against the bound; in completion mode the comparison is
made against the real anchor. Without this, a series running late would have kept
producing occurrences under the bound even though today's date had passed it.

**`1_canceled` regenerates the series, as in core.** `CLOSED_STATES` does not
distinguish "done" from "cancelled". This module does not touch that trigger;
removing it is a core behaviour change to be decided separately.

## Not for anniversaries

Completion anchoring is the wrong tool for date-bound recurrences — a birthday, a
statutory filing, a monthly rent. Those must stay on **From the deadline**, which
is the default, so no existing series is affected.

## Migration

None. `repeat_anchor` defaults to `deadline`, and in that mode the module
recomputes exactly what core would have computed. Existing recurrences do not
move.

## Tests

```
odoo -d <database> -u bf_recurrence --test-enable --test-tags /bf_recurrence
```

Fifteen tests covering both modes, both closing paths (state alone and folded
stage), early and late completion, a series without a deadline, the
`repeat_until` bound under both anchors, subtasks, the cancelled state, and the
task ↔ recurrence mirror.

## Requirements

- Odoo 18.0 Community
- `project`

## Licence

LGPL-3. See `LICENSE`.
