# Persona des contacts (`bf_persona`)

What to know before writing to a contact: register, salutation, the people who
belong in copy, and what the mail you exchange actually says about the
relationship. Everything shows up where it is useful, in the mail composer, and
is measured rather than declared.

## What it does

### Per-contact persona record

A `contact.persona` extends `res.partner` with:

| Field area | Purpose |
|---|---|
| Register and forms | `tu` vs. `vous`, preferred salutation and closing, language |
| What to know before writing | Free text repeated in the composer and in the AI context: the address to use, attachments rather than links, why this person is on first-name terms |
| Copy rules | Who goes in copy with this contact, and who must never be put in copy |
| Relationship, measured | Last message each way, volumes over 90 days, messages of ours left unanswered, median length of our last ten messages |
| Payer behaviour | Average payment delay computed from `account.move` history |
| Tone and KPIs | Observed tone both ways, custom KPIs, knowledge-matrix items shared with the contact |

A persona can be kept on a person or on a company; a company persona covers
everyone who works there.

### Composer integration

The banner reads the **recipients** of the message, in To and in Cc, plus the
persona of their company. For each of them: register, salutation, closing, and
the facts worth knowing before writing (a relationship to watch, messages left
unanswered, our own messages running long).

Then the copy rules of all those personas:

- **Missing mandatory copy** in red: someone who must be in copy and is not;
- **Usually included**: someone who is normally on this conversation;
- **Do not put in copy**: someone who asked not to be.

`Apply persona` does what can be done automatically: it greets and closes when
there is a single recipient to greet, adds the missing mandatory copies **in the
Cc field**, removes the people who must not be there, and reopens the composer so
the result is visible.

### Copy rules, suggested rather than guessed

A weekly cron reads the messages actually sent and proposes a rule when someone
is on at least four of them and on at least half. A suggestion does nothing
until a person confirms it in **Contacts → Personas → Copy rules**; a rejected
suggestion is never proposed again. What was measured is written next to the
rule, so the decision can be made on evidence.

### Seeding, and who is left out

The monthly seed creates personas for the people you correspond with: those who
wrote at least three times over ninety days, and those you wrote to as often,
counted **person by person** rather than by company. It leaves out your own
internal users, your own company, shared role mailboxes that name nobody (a
director writing from `info@` is a person, not a mailbox), and records whose
name is a raw email header. A persona archived by hand is never recreated.

### The relationship, measured

A daily cron records, for each persona: the last message received and sent, the
volumes over ninety days, the number of our messages sent since their last one,
and the median length of our last ten messages with quotes, signature and link
addresses excluded. A relationship is flagged **to watch** when at least two of
our messages have gone unanswered for at least a fortnight, or when a bridge
module raises a signal. Traffic going quiet is not, by itself, a degrading
relationship: a project that ends well ends quietly too.

### Background workers

| Cron | Cadence | Effect |
|---|---|---|
| Measure relationships | Daily | Recompute the measured facts and the relationship state |
| Recompute payment delay | Daily | Average delay from `account.move` history |
| Flag stale tones | Daily | Mark personas whose tone assessment is older than six months |
| Seed personas | Monthly | Create personas for active correspondents (at most 100 per run) |
| Suggest copy rules | Weekly | Propose rules from the messages actually sent |

### Dashboard

A kanban view at **Contacts → Personas → Dashboard** groups personas by
relationship state, with the reason, the unanswered count and the length of our
messages.

### AI context

`claude_context_summary` is computed on read and hands an assistant the register,
the forms, what to know before writing, the copy rules and the measured facts in
a few lines.

## Dependencies

| Module | Why |
|---|---|
| `contacts`, `mail`, `account` | Core Odoo |
| `mail_composer_cc_bcc` (OCA) | The composer's Cc and Bcc fields the copy rules write into |
| `project_knowledge_matrix` | KPIs link to knowledge items |
| `bf_onboarding_base` | Onboarding panel scaffolding |

`bf_persona_cx` plugs the customer-experience module in when both are installed.

## Changelog

### 18.0.3.1.0
- The assistant is told how long our mail to this contact usually runs, as a
  ceiling rather than a target. Above the long-message threshold it is asked to
  write shorter than that; below it, not to run past the measured median.
  Previously the figure only reached the assistant when we were already
  over-writing.

### 18.0.3.0.1
- Every field carries an explicit label. Half the form used to render in
  English on a French screen ("Partner", "Addressing Style",
  "Relationship Health"), because Odoo derives a label from the field name
  when none is given.

### 18.0.3.0.0
- The composer banner reads every recipient (To and Cc) and their company's
  persona, instead of the contact of the record it was opened on.
- Copy rules: mandatory or usual copies, and people who must never be copied;
  the category no longer gates them. `Apply persona` writes in the Cc field and
  reopens the composer.
- Copy rules suggested from the messages sent, with their evidence; nothing
  applies before confirmation.
- Seeding counts people rather than companies, and leaves out internal users,
  companies, role mailboxes and malformed records.
- Register, salutation and closing inferred from **our own** messages first.
- The relationship is measured daily on the messages themselves; the former
  traffic-drift detector and the automatic "last interaction" KPI rows are gone.
- New field: what to know before writing, repeated in the banner and the AI
  context.

### 18.0.2.2.1
- The banner rendered its own markup as text; register detection no longer
  counts words that merely start like `tu`.

## License


Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-08-12, this version converts automatically to
  **LGPL-3.0-or-later**.
