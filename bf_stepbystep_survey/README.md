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

The first click takes over the answer the company **already has**, or creates
that one answer, named after the party the page **names** (the beneficiary when
a bridge provides one, the ordering customer otherwise); every later click
reuses it. A **finished** answer is never reopened: the button disappears, the
page says *Questionnaire received*, and a click on a stale button leads back to
the page, so the answer's token stops travelling in addresses, bookmarks and
proxy logs. The redirect then hands over to the `survey` module, which renders
the questionnaire, resumes it at the right page and records the answers.

### The answer the company already has (v18.0.1.2.0)

A client often receives the questionnaire first through a personal link in an
e-mail. On the first click, the page takes over that answer instead of creating a
second one. It must be an answer to the same questionnaire, in the name of a
contact of the company the page names, created within the **90 days before the
click** (the invitation often leaves before the mandate exists), not a test
entry, with no passed deadline and no exhausted time limit, and held by no
mandate (archived ones included). The most advanced wins, then the most recent.
Its deadline is dropped: the page becomes the mandate's channel. If it is
already finished, the click attaches it and leads back to the page, which says
*Questionnaire received*. Each takeover leaves an internal note on the mandate.

Nothing is taken over when the page names the mandate's **payer** and that payer
looks like an ordering customer: another of its mandates names a different
beneficiary, or carries the same questionnaire. Its page names the payer, not the
client who reads it, and the answer of one of the payer's contacts would be
served to someone else. Without a bridge that names the beneficiary, any
other mandate of the payer counts. Name the beneficiary and the page finds the right answer.

When the answer taken over is not the right one, **Detach the answer** on the
*Client portal* tab (project administrators). The answer is kept, the client's
next click starts a new one, and from then on this mandate takes over nothing by
itself.

What it does not catch: an invitation sent to an e-mail address with no contact
behind it; a click more than 90 days after the invitation; an ordering customer
none of whose mandates names a beneficiary; and a contact who moves to another
company takes along an answer started within the window — the note on the
mandate and *Detach* are the remedy.

Two simultaneous clicks cannot create two answers: the route writes the project
row before looking, which makes the second transaction replay on a fresh snapshot
(the same technique the core uses for document deposits, and for the same reason
— Odoo runs in REPEATABLE READ, where a row lock is not enough).

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
- **Change Date**: on 2030-11-27, this version converts automatically to
  **LGPL-3.0-or-later**.
