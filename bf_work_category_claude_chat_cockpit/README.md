# BF Work Category: Gen cockpit (`bf_work_category_claude_chat_cockpit`)

Work category in the Gen cockpit. A bridge between [`bf_work_category_claude_chat`](../bf_work_category_claude_chat) and [`bf_claude_chat_cockpit`](../bf_claude_chat_cockpit).

## Installation

Nothing to do: the module installs itself as soon as `bf_work_category` and `bf_claude_chat_cockpit` are both present (`auto_install`).

## What it adds

A *Work category* column and filter in the Gen cockpit's own list (optional column) and search (*Work category* filter and *Group By*). The category itself comes from `bf_work_category_claude_chat`.

How the category is resolved, kept current and recomputed is described in the [`bf_work_category`](../bf_work_category) README.

## Translations

Source labels are in English; `i18n/fr_CA.po` ships French (Canada).

## License

Business Source License 1.1 (see `LICENSE`); each version becomes LGPL-3 on its Change Date.
