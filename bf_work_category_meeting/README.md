# BF Work Category: Meetings (`bf_work_category_meeting`)

Work category on meeting records, from their project. A bridge between [`bf_work_category`](../bf_work_category) and [`bf_meeting`](../bf_meeting).

## Installation

Nothing to do: the module installs itself as soon as `bf_work_category` and `bf_meeting` are both present (`auto_install`).

## What it adds

The resolved *Work category*, its *Category origin* and the *Override category* on the meeting record form, an optional column in the meeting list, and a *Work category* filter and *Group By* in its search.

How the category is resolved, kept current and recomputed is described in the [`bf_work_category`](../bf_work_category) README.

## Translations

Source labels are in English; `i18n/fr_CA.po` ships French (Canada).

## License

Business Source License 1.1 (see `LICENSE`); each version becomes LGPL-3 on its Change Date.
