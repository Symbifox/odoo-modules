# Progression Gantt (`bf_progression_gantt`)

A timeline view of a project's tasks, grouped by **progression stage** — the same
stages `bf_stepbystep_clients` defines.

## Why

Odoo Community has no Gantt view; it is an Enterprise feature. A project stage
model without a timeline is hard to read: you can see which stage a task sits in,
but not whether the stages overlap, nor which one is running late.

This is a hand-written OWL component, not a reimplementation of Enterprise's
view. It reads dates that already exist and draws them.

## What it provides

- Bars derived from existing fields, read-only: start is `date_assign` (falling
  back to `create_date`), end is `date_deadline`.
- Fill shows progress — hours spent against hours allocated — or task state when
  no allocation exists.
- Status colours: done, cancelled, late, in progress, upcoming.
- Rows grouped by progression stage, with abbreviated assignee names.

## Requirements

Odoo 18 Community, `project`, `hr_timesheet`, and `bf_stepbystep_clients` from
this repository.

## License

LGPL-3.
