# Workload Plan (`bf_charge`)

A task backlog is not a workload plan. Three things are missing between the two, and
this module does only those three.

## Why it exists

A backlog tells you how many hours are recorded. It does not tell you whether they fit,
when they land, or which of them are even real work. On a live database, most of the
budgeted hours in a project backlog usually turn out to be one of three things that a
plan must never count: templates waiting to be cloned, projects nobody has touched in
months, and quantities that came from a sale rather than from anyone's estimate.

## What it does

**A declared capacity, never inferred.**
Hours logged in a timesheet are not wall clock time. Assisted work produces several
hours within the same hour, so dividing a backlog by "hours logged per week" returns a
wrong answer with two decimals. Capacity is therefore declared, by hand, in a contract.
The module measures the candidates and shows them side by side (twelve month median,
mean, the declared working calendar, and a recent median capped to wall clock), then
lets a person choose.

**A workload that says where it came from.**
Odoo's allocated hours field has several sources: a human estimate, or a sold quantity
converted by its unit of measure. A yearly subscription line sold in "Year" lands in the
working time category and becomes thousands of hours that nobody estimated. Every task
therefore carries its **source**, and an hour derived from a sale unit that is not the
hour is set aside from the plan, visibly, with its reason.

**An unplaceable pile that is never hidden.**
A task with no date lands on no week. A calendar that only shows what it can place lies
by omission. The plan always shows, next to the weeks, the total hours it could not
place and how many tasks that is. On a real database that second number is often the
answer to the question being asked.

**Projects classify themselves.**
Live, dormant, or template. The project's last timesheet line is enough to separate what
moves from what sleeps, without reading a single message. Templates carry a tag: they
are patterns to clone, not work waiting, and their hours never enter a plan. An archived
project is filed away, whatever its last timesheet says.

**Five saturation signals.**
Concurrent customers against the declared ceiling, customer share against the target,
hour banks below zero, arrivals against closures, and the unplaceable backlog expressed
in weeks of declared capacity. Each one shows its measure, not just its colour.

**Slicing, on native views.**
Every computation writes a fact table: one row per task **and per week its workload lands
on**, carrying project, customer, source, dates and outcome. On top of it sit Odoo's own
views, not a hand drawn dashboard: a stacked bar chart of workload per week, a project by
week cross table, a monthly calendar of weeks against capacity, a coloured kanban of the
signals, and a trend chart over the readings. A custom front end component fails without
any server side test noticing; a standard view cannot lie about data the tests check.

## What it does not do

- It does not compute a capacity. It asks for one.
- It does not ask you to date your tasks. It **quantifies** what is not dated.
- It estimates nothing on anyone's behalf: a task with no estimate stays without one,
  and that is counted.

## Models

| Model | Role |
|---|---|
| `bf.charge.contract` | The declared capacity, with the measured candidates beside it |
| `bf.charge.plan` | Places what it can on weeks, and shows what it cannot |
| `bf.charge.plan.week` | One week, its workload, its capacity, its verdict |
| `bf.charge.plan.line` | One row per task and per week, the grain everything slices on |
| `bf.charge.plan.signal` | Five saturation signals, each with its measure |
| `bf.charge.snapshot` | The same reading, repeated and kept, so a trend exists |

`project.task` gains its retained workload, its source, the reason an hour was set aside,
its retained dates and whether it is placeable. `project.project` gains its kind and the
date of its last logged hour.

## Requirements

- Odoo 18 Community
- `project`, `hr_timesheet`

Optional, detected at runtime and never required: a Gantt module providing
`planned_date_begin` on tasks gives the plan a start date to spread from, and an hour
bank module makes the fourth signal report real balances instead of staying silent.

## Settings

| System parameter | Default | Effect |
|---|---|---|
| `bf_charge.template_tag` | `Gabarit` | Project tags marking a template. Several, comma separated |
| `bf_charge.dormant_days` | `90` | Days without a logged hour past which a project is dormant |

The weekly reading cron ships **disabled** on purpose: a reading taken before a capacity
is declared archives zeros that will later look like a measurement.

## Security

The module ships a single group, `Workload plan: management`. A person's capacity is
sensitive data and is not offered to every internal user. The group is deliberately
narrow, so the module reads timesheet lines and sale units with elevated rights on its
own behalf, each read explicitly bounded to its own company rather than relying on a
record rule that elevation would bypass.

## Licence

Business Source License 1.1. Each published version converts to LGPL-3.0-or-later on its
Change Date, four years after its release. See `LICENSE`.
