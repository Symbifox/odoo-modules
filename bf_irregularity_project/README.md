# Irregularities — Projects and timesheets (`bf_irregularity_project`)

Four detectors for `bf_irregularity`, over project tasks and timesheets. The
module adds no screen of its own: its findings appear in the registry that
`bf_irregularity` already provides.

⚠️ **It installs itself** as soon as `bf_irregularity`, `bf_task_waiting_states`
and `hr_timesheet` are all present, and the timesheet detector is **live from the
first hourly pass**: on a database with a month of history, expect one finding
per person and per working day with no time logged, over a five working day
window. The register stays quiet about it (no email, no to-do, unless you tick
**Schedule an activity**), but the list will not be short. Switch that detector
off, or widen its threshold, before the first pass if that is not what you
want.

## The detectors

| Detector | Family | Default | What it finds |
|---|---|---|---|
| Task waiting for too long | silent | 21 days, critical at twice that | A task left in “Waiting – Client” or “Waiting – External”. A task whose deadline, or whose planned activity, falls within the next 30 days is left alone: someone has already arranged to come back to it. |
| Open task in an archived project | orphaned | — | The project was archived and its open tasks went with it, out of every list. |
| Open task with nobody assigned | orphaned | **inactive** | Shipped off: many teams leave tasks unassigned on purpose (templates, ideas, notes). |
| Working day without a timesheet | silent | 5 working days back | An employee with an active user account and no timesheet line on a working day. |

Each one is data: change the threshold, the severity or the owner, or turn it
off, without touching the code.

### How the waiting time is measured

From the **last tracked change of the task's state** — the task is still in a
waiting state, so that change is when the wait began. A task whose state was
never tracked falls back to its creation date.

### What counts as a working day

The employee's own working calendar (`resource.calendar._works_on_date`), so
weekends and the calendar's days off are not working days. Failing a calendar,
Monday to Friday. Days covered by an **approved** time off are skipped too, when
`hr_holidays` is installed — without that, a week of approved vacation produced
five findings. The scan starts at the employee record's creation date and only
looks back over a window of working days (five by default): a day that leaves
the window stops being checked, so its finding stays open instead of resolving
itself.

## What the detectors read

Tasks, projects, employees and timesheet lines are read with elevated rights,
because a detection pass has to see what the person who triggers it cannot. Each
finding is owned by the person responsible for the record — the first active
assignee, else the project manager, else the detector's fallback owner — and the
registry's own record rule then shows each person only theirs.

The waiting detector pairs with `bf_task_waiting_states`, which is where the two
waiting states come from.

## Dependencies

`bf_irregularity`, `bf_task_waiting_states`, `hr_timesheet`. `hr_holidays` is
optional and only used if present. The French translation is in `i18n/fr_CA.po`.
