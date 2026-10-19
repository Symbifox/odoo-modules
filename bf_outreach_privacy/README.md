# Outreach privacy bridge (`bf_outreach_privacy`)

Connects outreach campaigns (`bf_outreach`) to the privacy register (`privacy_consent`),
for Québec's Law 25 (LPRPSP) and Canada's anti-spam law (CASL).

A prospect is a person who asked for nothing. `bf_outreach` keeps their name, role,
organisation, email, phone numbers, where the target came from, free-form notes and the
full history of calls, emails and letters, each with its own notes. On its own it declares
no purpose, no legal basis, no retention period and purges nothing. This bridge adds all
four, and gives a refusal a regime of its own.

## What it does

| What | Where |
|---|---|
| Declares the "Outreach and prospecting" purpose in the processing register, with its retention policy | `data/privacy_purpose_data.xml` |
| Adds two rules to the retention schedule, DEM-001 and DEM-002 | `data/privacy_retention_calendar_data.xml` |
| Keeps an opt-out list of hashes, written the moment a refusal is recorded | `models/outreach_optout.py` |
| Refuses to bring an opted-out person back: on creation, on import, on a contact edit, and at the last-minute solicitation check | `models/outreach_target.py`, `models/outreach_target_import_wizard.py`, `models/res_partner.py` |
| A daily scan that **proposes** expired targets in a destruction campaign left in review | `models/outreach_target.py`, `data/bf_outreach_privacy_cron.xml` |
| Real destruction (touches and their attachments included) and a scope statement in the destruction register | `models/privacy_destruction_campaign.py`, `models/privacy_destruction_register.py` |
| Makes targets and touches classifiable | `models/privacy_document_classification.py` |

Nothing is destroyed until the privacy officer approves, then runs, the campaign. The
campaign writes the destruction register, and names the target by its number only.

## Retention

**DEM-001, a target that did not refuse: three years after the last useful contact.**
A useful contact is a touch that reached the person (reached, replied, interested,
politely declined, call-back requested) or any inbound touch. Without one, the period
runs from the target's creation. Unanswered attempts extend nothing; otherwise a target
called every month would never expire. Three years matches correspondence (COR-001).

**DEM-002, the record of a person who refused: proposed without delay.** A refusal needs
its own regime. Destroying the whole record would lose the proof of the refusal; keeping
it whole would contradict the refusal. The proof therefore lives in the opt-out list, and
the record goes. The real delay is the time it takes to approve the campaign.

Each campaign line is checked again when the campaign runs. A target that answered in the
meantime, or whose refusal was lifted, is skipped instead of being destroyed under an
expiry that is no longer true, and comes back once it is due again. A target that refused
while its line was waiting is destroyed whatever the line's rule. A line skipped **by
hand** holds the target for a year: that is how the officer keeps a record.

## The opt-out list

When a refusal is recorded (the target's "Do not contact" button, the contact's checkbox,
or an exclusion requested by a federated client), the bridge stores:

- a SHA-256 hash of each address in the email field (normalised), and of the phone and
  mobile numbers in E.164, each separately;
- the date, the user who recorded it, the reason in the person's own words, and the
  record it came from.

The contact details themselves are not stored. To check an address or a number, type it
in the search box (Vie privée › Opposition au démarchage, or Démarchage ›
Configuration). This is
pseudonymisation, not anonymisation: whoever already has the address can check that it is
listed. ⚠️ A phone-number hash can be reversed by brute force in minutes (the number
space of an area code is small), so the hash field is readable by administrators only.
The search itself remains an oracle, which is why it is limited to privacy users and
outreach managers.

**Sibling records** (the same person in another campaign, matched on a shared contact
detail) are no longer solicited: `bf_outreach` runs `_is_solicitable` before every
follow-up activity and every send, and the bridge makes it consult the list. They are not
marked "Do not contact": such a freeze would spread from one contact detail to the next
(a shared switchboard number is enough) and send people who refused nothing to DEM-002.

An entry has **no expiry**: removing it would reopen the door it closes. It is lifted when
an **outreach manager or the privacy officer** lifts the refusal on the target or on the
contact (the person gave their consent again); a plain user cannot. A lift only removes
the entries the lifted record owns. Entries from an earlier refusal stay, even if that
record has been destroyed since, and the lifted record says so. When another live refusal
carries the same contact detail, the entry is handed over to it, with that refusal's
date, author and reason. The privacy officer can also delete an entry by hand.

Every deletion of a target goes through the same door, whatever the path (destruction
campaign, manual deletion, deletion of the outreach campaign, erasure request): touches
and attachments through the ORM, the local copy of the federated report, and a refusal to
delete a refused target when nothing else would keep the refusal.

⚠️ **The block applies to the whole database**, across campaigns and companies. That is
stricter than CASL, which is about the sender, and it is deliberate.

## What destruction does not reach

This statement is added to every register entry about a target (a touch destroyed on its
own has its own statement):

1. the linked Odoo contact, which keeps its name, its contact details and, where relevant,
   the record of its refusal, and the opportunity created from the target, whose
   description copies the touch history; both follow their own regime;
2. emails sent (a copy lives with the recipient and in our sent folders), letters handed
   over, and calls and emails archived elsewhere, under their own rules;
3. the mirror of the federated report on a client's side, removed at its next refresh only
   if the report is still shared;
4. backups, which keep the previous state for as long as their own rotation does.

The declared method is "delete", never "secure wipe".

## Installation

No `auto_install`: a retention period is a business decision, made by each database's
privacy officer. Install it by hand. On installation, refusals already recorded (archived
targets included, and contacts marked "Do not contact") are copied into the opt-out list.

The cron "Démarchage — proposition de destruction (DEM-001/002)" ships active. Batch size:
the `bf_outreach_privacy.batch_size` parameter (500).

## Dependencies

- `bf_outreach` (Business Source License 1.1)
- `privacy_consent`

## Languages

The module is written in French, its source language. It ships no `en_CA` catalogue yet.

## Testing

```
odoo -d <db> --test-enable --test-tags /bf_outreach_privacy --stop-after-init
```

⚠️ The tests run in a rolled-back transaction because the destruction register is
immutable. A rolled-back transaction still consumes numbers from the register's sequence:
do not run the tests on a production database, they would leave gaps in its numbering.

## Licence

Business Source License 1.1, converting to LGPL-3.0-or-later; see `LICENSE` for the
Change Date and the additional use grant.

## Changelog

- **18.0.1.0.0**: first release. Purpose, DEM-001 and DEM-002 rules, opt-out list of
  hashes, scan that proposes, verified destruction recorded in the register,
  classification.
