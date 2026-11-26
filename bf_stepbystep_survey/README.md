# bf_stepbystep_survey — Questionnaire on the client progression page

A bridge module for `bf_stepbystep_clients`: it puts a **questionnaire button**
on the client's progression page and ties the answer to the **client of the
mandate**, so the filled questionnaire lands in that client's file instead of an
anonymous pile.

It is a bridge, not part of the core, because `survey` is not installed on every
tenant that serves a progression page, and the core must not require it.

## What it adds

- Two fields on the project (**Client portal** tab):
  | Field | Description |
  |-------|-------------|
  | `progression_survey_id` | The questionnaire the client opens from the page. |
  | `progression_survey_stage_id` | The stage whose block carries the button. Empty: the button shows once, under the page heading. |
- A **POST** route, `/progression/<project>/<token>/questionnaire`, under the
  **same guard** as the page itself (project published, token right, project
  active; every refusal answers 404). POST with the page's CSRF token, because
  the gesture creates the answer and nothing that merely follows a link — a link
  prefetcher, an email scanner, a crawler — may create one.
- On the page: the button while the questionnaire is open, a *Questionnaire
  received* badge once the client has finished it.

## How the answer is tied to the mandate

The answer is keyed on the **mandate**, through `progression_survey_answer_id` on
the project — never on `(questionnaire, partner)`. Under an ordering customer,
two mandates carry the same `partner_id`: a key by partner would hand the second
client the first one's answer, and let them overwrite it. The field does not copy
with the project, so a copied mandate is not born "already answered", and
changing the mandate's questionnaire detaches the old answer.

The first click creates that one answer, named after the party the page **names**
(the beneficiary when a bridge provides one, the ordering customer otherwise),
and every later click reuses it. A **finished** answer is never reopened: the
button disappears, the page says *Questionnaire received*, and the route itself
answers 404, so the answer's token stops travelling in addresses, bookmarks and
proxy logs. The redirect then hands over to the `survey` module, which renders
the questionnaire, resumes it at the right page and records the answers.

Two simultaneous clicks cannot create two answers: the route writes the project
row before looking, which makes the second transaction replay on a fresh snapshot
(the same technique the core uses for document deposits, and for the same reason
— Odoo runs in REPEATABLE READ, where a row lock is not enough).

## Guards worth knowing

- A stage from another project is refused at save time: the page only renders its
  own project's stages, so such a stage would show no button at all.
- A questionnaire with **Require Login** is refused: the client of a progression
  page has no account, which is the whole point of the page. *Invited people
  only* is fine — the route always passes an answer token.
- A stage that is internal (not *Visible on client portal*) or without a
  progression step number shows nothing, and the route refuses it: the client
  does not open ahead of time what will be asked later, which is the rule the
  core already applies to document deposits.
- Archiving the questionnaire, or ticking **Require Login** after linking it,
  closes the button at render time — the save-time constraints cannot see a later
  write on the questionnaire itself.

## What the adviser sees

The project's **Client portal** tab carries the questionnaire, the stage that
asks it and, read-only, the mandate's answer — the one path from the mandate to
the filled questionnaire. To detach an answer and let the client start over,
delete that `survey.user_input`: the field falls back to empty and the button
returns. Both fields are reserved to **Project: Administrator**, the same group
the core requires to publish a page: linking a questionnaire to an already
published page exposes its questions to the client, so it is an access, not a
caption.

## The mandate manager is notified (v18.0.1.1.0)

When the client completes the questionnaire from the page, the project manager
of the mandate is notified, through the channels chosen in **Progress >
Configuration > Settings** (see the core's README). The e-mail names the
questionnaire and links to the project; the answers stay in Odoo.

## Installation

```bash
odoo -d <database> -i bf_stepbystep_survey --stop-after-init
```

Odoo dependencies: `bf_stepbystep_clients`, `survey`.

⚠️ **It needs `bf_stepbystep_clients` 18.0.2.6.0 or later**, and 18.0.2.8.0
or later for the notice to the mandate manager (on an older core the answer
is still recorded, and nobody is notified). Its two xpath
anchors (`page_extra` on the page, the named group on the form) do not exist in
2.5.0, and `depends` cannot carry a version floor: installing it against an
older core fails with a `ParseError`, which in a grouped upgrade takes the whole
run down with it.

⚠️ Installing `survey` on a tenant that does not have it also brings
`gamification` (badges and challenges menus for every user, plus one daily cron).
That is the reason this module is a bridge rather than part of the core.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-11-25, this version converts automatically to
  **LGPL-3.0-or-later**.
