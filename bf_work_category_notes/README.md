# BF Work Category: Notes (`bf_work_category_notes`)

Work category on notes, from the record the note is attached to: a task, a project, or any record that carries a category. A bridge between [`bf_work_category`](../bf_work_category) and [`bf_bloc_notes`](../bf_bloc_notes).

## Installation

Nothing to do: the module installs itself as soon as `bf_work_category` and `bf_bloc_notes` are both present (`auto_install`).

## What it adds

The resolved *Work category*, its *Category origin* and the *Override category* on the note form, an optional column in the note list, and a *Work category* filter and *Group By* in its search. A category change never moves a note's *Last Updated on*, so it does not reorder the notes nor make an edit on the phone look like a conflict.

How the category is resolved, kept current and recomputed is described in the [`bf_work_category`](../bf_work_category) README.

## Translations

Source labels are in English; `i18n/fr_CA.po` ships French (Canada).

## License

LGPL-3 (see `LICENSE`).
