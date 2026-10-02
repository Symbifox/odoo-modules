# BF Work Category: Gen (`bf_work_category_claude_chat`)

Work category on Gen conversations, from the record the conversation is attached to: a task, a project, or any record that carries a category. A bridge between [`bf_work_category`](../bf_work_category) and [`bf_claude_chat`](../bf_claude_chat).

## Installation

Nothing to do: the module installs itself as soon as `bf_work_category` and `bf_claude_chat` are both present (`auto_install`).

## What it adds

The resolved *Work category*, its *Category origin* and the *Override category* on the conversation form, an optional column in the conversation list, and a *Work category* filter and *Group By* in its search.

How the category is resolved, kept current and recomputed is described in the [`bf_work_category`](../bf_work_category) README.

## Translations

Source labels are in English; `i18n/fr_CA.po` ships French (Canada).

## License

Business Source License 1.1 (see `LICENSE`); each version becomes LGPL-3 on its Change Date.
