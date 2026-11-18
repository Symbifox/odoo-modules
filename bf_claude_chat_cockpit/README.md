# Gen — Cockpit

The administration view of Gen sessions, split out of `bf_claude_chat`.

`bf_claude_chat` carries the chat. This module carries what you look at when
you wonder whether something is wrong: the sessions with their stream failure
counter, and token usage.

Both screens sit under `base.group_system` and belong to Gen's administration
menu. Not installing this module takes nothing away from the chat.

## Screens

| Menu | Model | What it shows |
|---|---|---|
| Cockpit | `claude.chat.session` | sessions, `stream_fail_count`, `last_stream_error`, a reset button |
| Usage | `claude.chat.message` | tokens and API-equivalent cost, per person and per week |

Three consecutive failures on a session: it stops resuming its Claude thread
and forks a fresh one on the next message. The counter is cleared by hand, once
the cause is understood.

## Languages (18.0.1.1.0)

The module is written in English, and `i18n/fr_CA.po` now carries the French
translation of its field labels, help texts, selection values, view labels and
user-facing messages: a user whose language is set to French reads the module in
French, and an English-speaking user is unaffected.

The manifest summary and description were the last French strings of an English
module; they now read in English, and the French summary lives in the
catalogue. A module description is not translated — Odoo reads it from the
manifest on disk.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Change Date**: on 2030-11-17, this version converts automatically to
  LGPL-3.0-or-later.
