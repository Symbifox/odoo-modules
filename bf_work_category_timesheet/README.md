# BF Work Category: Timesheets (`bf_work_category_timesheet`)

Work category on timesheet lines, from their task, then the line's own project (time entered without a task). A bridge between [`bf_work_category`](../bf_work_category) and `hr_timesheet` (Odoo's Timesheets).

## Installation

Nothing to do: the module installs itself as soon as `bf_work_category` and Odoo's `hr_timesheet` are both present (`auto_install`).

## What it adds

The resolved *Work category*, its *Category origin* and the *Override category* on the timesheet form, an optional column in the timesheet lists, and a *Work category* filter and *Group By* in their search, so the timesheet pivot can show hours by category.

How the category is resolved, kept current and recomputed is described in the [`bf_work_category`](../bf_work_category) README.

## Translations

Source labels are in English; `i18n/fr_CA.po` ships French (Canada).

## License

LGPL-3 (see `LICENSE`).
