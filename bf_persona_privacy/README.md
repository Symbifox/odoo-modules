# Contact persona privacy bridge (`bf_persona_privacy`)

Connects contact personas (`bf_persona`) to the privacy register (`privacy_consent`),
for Québec's Law 25 (LPRPSP).

A persona holds, about a named person, a relationship profile (form of address,
salutation, what to know before writing, personal details, perceived tone, measured facts
about the relationship, copy rules, indicators) and an assessment of their
creditworthiness (payer quality, payment notes, average payment delay, indicators drawn
from accounting). On its own, `bf_persona` declares no purpose, no legal basis and no
retention period, and purges nothing: the persona of a client who left stays forever,
payer rating included. This bridge adds all of that.

## What it does

| What | Where |
|---|---|
| Declares the "Profil relationnel des contacts et cote de payeur" purpose in the processing register, with its retention policy | `data/privacy_purpose_data.xml` |
| Adds rule PER-001 to the retention schedule | `data/privacy_retention_calendar_data.xml` |
| A daily scan that **proposes** expired personas in a destruction campaign left in review | `models/contact_persona.py`, `data/bf_persona_privacy_cron.xml` |
| Complete destruction (indicators, copy rules, thread and tracked history) by every path: campaign, persona manager, deleted contact, merged contacts, erasure request | `models/contact_persona.py`, `models/res_partner.py`, `models/base_partner_merge.py`, `models/privacy_destruction_request.py` |
| An "À vérifier (persona)" (to check) column on campaign lines | `models/privacy_destruction_campaign.py`, `views/privacy_destruction_campaign_views.xml` |
| Real destruction by campaign, and a scope statement in the destruction register | `models/privacy_destruction_campaign.py`, `models/privacy_destruction_register.py` |
| Makes personas, indicators and copy rules classifiable | `models/privacy_document_classification.py` |

Nothing is destroyed until the privacy officer approves, then runs, the campaign. The
campaign writes the destruction register, and names the persona by its number only. Each
line is checked again when the campaign runs: a persona whose person has written since,
or who has an unpaid invoice, is skipped instead of being destroyed under an expiry that
is no longer true, and comes back once it is due again. A line skipped **by hand** holds
the persona for a year: that is how the officer keeps one.

## Retention, and why

**PER-001: three years after the business relationship ends.** It matches correspondence
(COR-001): the persona is drawn from email, so it should neither outlive that email nor
die before it. A client who comes back after a year or two of silence finds their persona
again.

## When the relationship ends: the person goes silent

No declarative signal held up in practice: an archived project, or one in a "Done"
stage, is often still active. Only silence tells. The anchor date is therefore the last
sign of life, among:

- the last email or portal message **written by the person**, in a record's thread or in
  the unified inbox (`bf_email_management`, when installed: much inbound email never
  reaches a thread);
- the last message **we sent them**, whether they were a recipient or only a follower of
  the record (the email notification proves it; a failed notification does not count),
  sent by an internal user or in the company's name, notices included (type
  "notification"); internal notes are left out;
- for the **account holder** (a company, or an individual invoiced in their own name):
  the date of their last posted invoice or credit note.

**Who "the person" is.** The persona's contact; the other contacts that carry the same
address, archived ones included (a duplicate sometimes keeps writing after the persona's
contact has gone quiet); and the address itself, for an email whose author was not
recognised (exact address, or between angle brackets; in the unified inbox, through its
participants: an address that merely contains it does not count). For a **company**
persona, anyone at the company. For an **individual's** persona, only their own
exchanges count: an employee who left a client that is still active is proposed for
destruction, even though the client stays. Without any sign, the persona's creation date.
A persona is never dated before its own creation. The persona of an internal user, or of
one of our own companies, is never proposed: its silence cannot be measured this way.

⚠️ A shared address (a generic mailbox) links every contact that carries it: their
exchanges and their unpaid invoices extend each of those personas. That is the cautious
direction (keeping longer).

**Never while an invoice is unpaid.** A posted customer invoice, unpaid or partly paid,
addressed to the contact (for a company: to it or to its people), holds the persona: the
relationship is not over, and the payer rating is still in use.

An **archived** persona follows the same rule: archiving destroys nothing.

## What the officer sees before approving

The campaign line only says "Persona #N", and the privacy officer has no persona role. A
computed, **non-stored** column, "À vérifier (persona)", shows the contact, their last
signs of life by source, the creation date, and an unpaid invoice if there is one. Only
the officer sees it; it is computed only for pending lines of a campaign still to be
decided (each line takes the measure again); it disappears with the persona; and nothing
from it reaches the register.

## What destruction takes

The persona, its profile, its payer rating, its indicators and its copy rules (through
the ORM, so their attachments go too), its discussion thread (including the tracked
history of the payer quality), its activities, its followers and its attachments. The
register names it by its number only.

🔴 **A deleted contact takes its persona through the ORM.** `partner_id` is
`ondelete="cascade"`: without the bridge, deleting the contact erased the persona in SQL
and left its thread orphaned, payer-rating history included.

🔴 **So does a contact merge.** `bf_persona` allows one persona per contact: when two
merged contacts each had one, Odoo deleted the source persona in SQL. The bridge keeps the
destination contact's persona (failing that, the first one) and deletes the others through
the ORM before the merge.

🔴 **The right to erasure.** An erasure request first classifies the person's persona
(otherwise it ignored it), both when the request is created and when it runs (a request
entered by hand never went through creation). It then **deletes** the persona itself,
whatever the method: with "delete", the base module would have skipped the persona because
the officer has no write access to it; and without a policy, the base module falls back to
"anonymize", which would only have archived a persona (profile, payer rating and history
intact) while the register certified an anonymisation. The request says so in its log and,
when the requested method was not a deletion, in its notes as well, which the register
picks up. Only the "manual" method is left alone.

After a destruction, the persona's classifications are archived and no longer point to the
person (subject and notes cleared): the register points to the campaign, the campaign to
the line, the line to the classification. The register records the persona by its number
only, with its categories (`financial`, `identification`).

## What destruction does not cover (and the register says so)

1. The Odoo contact itself, which follows its own rules.
2. The emails, invoices and payments the persona is drawn from, under their own rules
   (COR-001, FIN-001): the payment delay can be recomputed from them.
3. Project knowledge items the persona referred to.
4. Copy rules of OTHER personas that name this contact in copy.
5. What was written with the persona's help: emails and letters sent, assistant
   conversations that received its summary.
6. Backups, for as long as their own rotation keeps them.

The declared method is "delete", never "secure wipe".

If the person gets back in touch, seeding may create a new persona from their new
exchanges (3 emails in 90 days): that is a new relationship, not the old record coming
back. No tombstone is needed.

## Who sees what

Unchanged by the bridge: `bf_persona` 18.0.3.3.0 settles it. The persona and its summary
are reserved for the "Utilisateur persona" (persona user) group; the payer rating, the accounting indicators and
the personal details for the "Gestionnaire persona" (persona manager) group. The writing assistant
(`bf_claude_chat`, an AI service) receives the persona's summary when a persona user asks
it for help, never the payer rating: the purpose's public text says so. The privacy officer
needs no persona role to run a campaign: approving the campaign is the control.

## Installation

No `auto_install`: a retention period is a business decision, taken by THIS company's
privacy officer and recorded in ITS register. Install it by hand, database by database.

Setting: `bf_persona_privacy.batch_size` (500 by default), the maximum number of personas
proposed per cron run.

The register texts (purpose, public summary, retention rule) are in French, the language
of the Québec register they feed.

## Details that are expensive to rediscover

- The cron is `noupdate="1"`: an upgrade does not switch it back on if it was turned off,
  and rewrites neither its name nor its `code`. Renaming the method it calls REQUIRES a
  migration.
- `_execute_destruction` hands over to `super()` for models it does not own: several
  bridges override this method and only coexist through that hand-over. A second guard, on
  `privacy.destruction.register.create()`, refuses to certify that a persona still in the
  database is gone.
- Archiving rule PER-001 suspends the regime: no more proposals, and an already approved
  campaign no longer runs anything under it (lines skipped). It is still recognised as the
  bridge's own rule.
- A line without a rule, or whose rule was cleared, is checked again under PER-001.
- Every day the cron adds its newly expired personas to the SAME campaign in review: a line
  that arrived after the review is approved with the others. The "À vérifier" column is
  recomputed when the campaign is opened.
- Every scan method starts with `_`: none can be called over XML-RPC. To try one, use
  `odoo shell`.
- The tests build their invoices in SQL (state, date, payment): a real posting depends on
  the database's chart of accounts.

## Changelog

- **18.0.1.0.0**: first release. Purpose and retention policy in the register, rule
  PER-001, a daily scan that proposes, verified destruction recorded in the register,
  personas, indicators and copy rules made classifiable.

## License

Business Source License 1.1, see `LICENSE`.
