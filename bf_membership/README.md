# Membership (`bf_membership`)

An association knows who it serves. It rarely knows, without opening three
files, who is a member in good standing today, since when, who speaks for a
member organisation, and who has not renewed.

This module keeps that register, for an association, a non-profit, a
federation of organisations or a co-operative. It does **not** depend on an
Odoo invoice to know who is a member: the payment is recorded, whatever its
source. Odoo's own `membership` module takes a member's state from an invoice
(draft, posted, paid), so an organisation that collects its fees elsewhere has
no members in it; that module was also removed in Odoo 19.

The interface, the email template and the printed list are in French; the
labels below are quoted as they appear on screen.

## The membership suite

This module is the base. The add-ons extend its models and leave it unchanged.

| Module | What it adds |
|---|---|
| `bf_membership` | The register: categories, memberships, member numbers, delegates, renewals, reminders, list import |
| `bf_membership_account` | The membership fee invoiced in Odoo, and the official receipt a registered charity issues for the eligible part of a fee |
| `bf_membership_portal` | The member's portal page, online signup, the membership card and an opt-in members directory |
| `bf_membership_assembly` | General meetings: voter list at a record date, notice, attendance, quorum, proxies, votes, minutes |
| `bf_membership_assembly_governance` | Bridge to `bf_corporate_governance`: an adopted proposal becomes a resolution in the corporate register |

A professional-order add-on (public roll, continuing education, insurance,
inspection and discipline under Québec's Professional Code) is planned. It is
not part of this release.

## Models

| Model | What it holds |
|---|---|
| `bf.membership.type` | The category: who may join (a person, an organisation, or either), fee, period (fixed fiscal year, rolling, lifetime), voting right, admission (automatic or by decision), grace period, renewal lead time, how many delegates an organisation may designate |
| `bf.membership` | One membership for one period, with **two separate states**: the membership and its payment |
| `bf.membership.delegate` | A person who represents a member organisation: role, voting flag, dated mandate |
| `res.partner` (extended) | Member number, current status, member since, in good standing until, two consents, delegates |

## Seven decisions, and why

### 1. Payment is recorded, not derived from an invoice

The person who receives the fee records it on the membership, with its date,
a reference and its source: invoice, online payment, Zeffy, Stripe, cheque,
cash, transfer, external platform or other. When the accounting lives in Odoo,
`bf_membership_account` lets the invoice drive this field instead.

### 2. Two states, not one

`state` says where the membership stands: « Demande » (request), « À payer »
(awaiting payment), « En règle » (in good standing), « Échue » (expired),
« Retirée » (withdrawn), « Refusée » (refused). `payment_state` says whether
the fee is settled: to pay, paid or exempt.

An honorary member is in good standing without paying (exempt). A request
awaiting a decision can already carry its payment, and accepting it then puts
it in good standing at once. Recording a payment moves « À payer » to
« En règle »; reversing it (a bounced cheque) moves it back.

A category with automatic admission accepts a membership as it is created; a
category « Sur décision » (by decision) leaves it in « Demande » until someone,
usually the board, accepts or refuses it.

**The state changes only through the membership's actions** (accept, refuse,
put a refused request back under review, record the payment, exempt, withdraw,
renew), which check the person's access
rights. Writing the state directly is refused, and outside superuser a
membership is always created as a request (automatic admission then accepts
it through the same action): otherwise "in good standing" could be set without
any payment. The state, who decided and when, the withdrawal, the reminder
stage, the import source and reference, the renewal link and the history flag
below can be set at creation neither in the values nor through a `default_*` context
key. The list import and the renewals create their memberships through the
module's own code.

A membership that is paid, exempt or has **ever** made someone a member keeps
its member, its category and its company: otherwise a receipt, a card or a
vote would move to someone other than the person who paid. "Ever" is a flag
set at the first good standing and never cleared, so putting the payment back
to "to pay" does not reopen the membership to another person.
(`bf_membership_account` adds: a membership carried by a posted invoice.)

### 3. The period is described on the category

Fixed fiscal year (for example 1 April to 31 March), rolling (a number of
months from the start date) or lifetime. A membership taken mid-year ends with
the fiscal year. The end date is computed and stays editable: a board-approved
extension is typed on the membership. A fiscal year that starts on 29 February
falls back to the 28th in common years.

### 4. Nobody who was a member leaves the register

Section 104 of Québec's Companies Act (Part III) requires the register to list
every person who **is or has been** a member, with address and occupation. So:

* a membership that ever made someone a member can be neither refused nor
  deleted, even after its payment was put back to "to pay" (a bounced
  cheque), and a contact that holds one cannot be deleted either (archive
  it). An imported line that expired without ever being paid made no one a
  member, and can be deleted;
* leaving is a state, « Retirée », with a date and a reason entered in the
  « Retirer » wizard;
* « Ancien membre » (former member) means having been in good standing at
  least once, even if that payment was later reversed: a request withdrawn
  without any payment does not put anyone in the register of former members;
* the « Registre » keeps a former member who applies again, while the new
  request is still open.

The reverse also holds: what never made anyone a member can go. A request, or
an accepted membership that was never paid (an abandoned online request, a
person who never followed up), can be refused with « Refuser »; « Remettre à
l'étude » puts a refused request back under review. A request, or a refused
membership, that was never paid can be deleted by a manager. A contact that
carries any membership, a mere request included, is deleted only by a
membership manager, and only if all its memberships are of that kind; they
are then deleted with it.

Neither the module's messages nor its refusals tell someone without a Members
role that a person applied or is a member:

* when such a user, allowed to delete contacts, deletes one that carries
  membership data (a membership, a delegation, and whatever the add-ons add: a
  voter line, a public application), the contact is archived instead, without
  an error, and the membership or the delegation logs it. A user who may not
  delete contacts gets Odoo's own refusal, the same for every contact, and so
  does a contact linked to an active user;
* Odoo's mail followers and notifications name people: any employee can read
  them for any record. For the association's models, only the Members roles
  read them; everyone still reads their own. A person who receives a message
  posted on a membership (a colleague the team writes to, for instance) sees,
  as in any Odoo message, who else it notified;
* writing a membership field on a contact, or creating a contact with one, is
  refused with the same message whether or not the person is a member, and the
  `default_*` context keys for these fields are ignored.

Some inferences remain possible for a user who manages contacts without the
role: a merge refused where another succeeds, a contact archived instead of
deleted (it shows under the « Archived » filter). Give contact-management
rights sparingly in an association whose membership is sensitive.

### 5. The member number comes with the first good standing

A contact gets its member number the first time one of its memberships is in
good standing (or settled), not when it applies or is accepted: a request that
is never paid never uses a number. Numbers come from a gap-free sequence
shared by the whole database, which skips numbers already taken (by an import,
for instance). They belong to the contact and are kept for good: a former
member who comes back gets the same number.

Only a membership manager can set a member number when creating a contact, or
change one afterwards.

Merging contacts with Odoo's merge wizard rewrites their links directly in the
database, so a merge that touches a contact carrying a membership or a
delegation is reserved to the Members roles; anyone else gets a refusal that
says no more than "not with your rights". Each membership moved by a merge
logs it. The result keeps a single number: the
destination contact's, or, if it has none, the number of a merged contact, so
that number is not lost. The merge also **never copies the consents of a
merged contact**: the kept contact keeps its own, so a duplicate typed by a
stranger in a public form cannot tick them on the real person's behalf.

The status fields on a contact (status, current membership, member since, in
good standing until) are computed: writing them is refused outside superuser.
A status set by hand would otherwise open the membership card and the
directory.

### 6. An organisation has one vote, carried by one delegate

An organisation does not vote; a person votes for it (Canada Not-for-profit
Corporations Act, s. 154(6)). The category sets how many delegates a member
organisation may designate at the same time (0 means no cap). At any date
**only one of them carries the vote**; the others are non-voting delegates or
substitutes (« Substitut »). Delegates live on the organisation's contact, not
on the membership, so they do not change at each renewal, and each mandate is
dated. A delegate must be a person, and only an organisation has delegates.

### 7. Nothing is sent by default

Preparing renewals and sending reminders are both **off at installation**,
per company. Each of the four reminders (first, second, end date, end of the
grace period) goes out at most once per membership, and a reminder that is
more than three days late is skipped, not sent: turning reminders on,
or importing a list, does not wake up every member whose end date is already
past.

## The daily pass

A scheduled action runs once a day over every company:

* it expires memberships (in good standing or awaiting payment) whose end date
  has passed;
* if the company turned it on, it creates the next period's membership for
  memberships that end within the category's renewal lead time (30 days by
  default). The renewal starts the day after the end date, awaits payment (or
  is exempt when the category is free) and does not go back to the board;
* if the company turned them on, it sends the renewal reminders: a first and a
  second reminder (30 and 7 days before the end by default), one on the end
  date, and one at the end of the grace period. Reminders go only to paid or
  exempt memberships whose contact has an email address, and stop once a
  renewal is in good standing or the member has withdrawn;
* it recomputes every member's status, which depends on today's date.

The status on the contact is one of « Membre en règle » (current),
« En grâce » (expired, within the category's grace period, 30 days by default),
« En attente » (a request or a payment is pending), « Ancien membre » (former)
and « Non membre ».

## Importing a list

« Configuration > Importer une liste »: a CSV file (UTF-8 or Windows-1252;
comma, semicolon or tab) with one header row, in French or English. Recognised
columns: email, first name, last name, full name, organisation, member number,
category, start date, end date, amount, paid, payment date, identifier, phone,
address, city, postal code, occupation. Columns it does not recognise are
listed in the report.

* Contacts are matched in this order: member number, email (exact,
  case-insensitive), then name and postal code. Two contacts answering to the
  same email (or the same name and postal code) are a **conflict**: the row is
  set aside and named in the report, never attached at random. A matched
  contact only gets its empty fields filled in.
* The category column is matched against category codes, then names; when it
  is empty or unknown, the wizard's default category applies.
* Re-importing the same source with the same identifier updates the
  membership (payment, amount, end date) instead of creating a second one. A
  row whose period is already in the register is counted as a duplicate and
  skipped.
* A row whose period has already ended is created « Échue », with its
  reminders marked as done.
* « Aperçu » (preview) runs the real import and rolls it back. Because the
  member-number sequence is gap-free, a preview consumes no number.

No export from an actual third-party membership or donation platform has been
run through the import yet: check the recognised headers against your first
real file.

## The register and the annual member list

« Registre » lists current and former members, archived contacts included,
with number, name, occupation (the contact's job position), address, member
since, in good standing until, and status. The « Liste des membres » PDF
(Companies Act s. 223: a list per year that members may consult) prints from a
selection of contacts, in alphabetical order, with number, name, address,
occupation, category and status. Neither is public: this module has no public
page.

## Consents

Two flags on the contact, both unchecked by default (Québec Law 25, s. 9.1:
the most protective settings by default). Every change, by staff or by the
member on the portal (`bf_membership_portal`), is logged with who made it on
the member's most recent membership in each company where the contact holds
one, so that each association reads it at home. It is not logged in the contact's
history, which any employee reads and where even a message with hidden values
would show that the membership team was there. The context keys that silence
tracking do not silence this log. A consent is the person's, and counts for
every association of the database: only someone who holds every company where
the contact has a membership writes its consents and its number (a request
created for the occasion in one's own company does not open the others). A
contact that holds no membership yet logs nothing. Duplicating a contact never copies
its consents:

* « Paraît au répertoire des membres »: read by the directory of
  `bf_membership_portal`;
* « Accepte les avis par courriel »: read by `bf_membership_assembly` to send a
  meeting notice by email rather than by post.

## Security and access

| Group | What it can do |
|---|---|
| « Agent » | Keep the register: memberships, payments, acceptance and refusal, delegates, withdrawals, the register and the member list. Reads categories. Cannot delete a membership |
| « Responsable » (manager) | Everything an agent does, plus categories, list import, deleting a membership that never made anyone a member or a contact that carries a request, and changing a member number. The default administrator account gets it at installation |
| Odoo administration (Settings) | « Configuration > Réglages » is Odoo's Settings: renewal preparation and reminders are turned on by an administrator |

Odoo's `membership` module reserves everything to the invoicing group; the
person who welcomes members is not necessarily the one who keeps the books.

* An employee with neither role sees no membership, and does not see on a
  contact whether it is a member, its member number, its consents or its
  delegates. Belonging to an association can reveal a person's health, beliefs
  or allegiances, which Québec's Law 25 protects; those contact fields are
  restricted to the Members roles. Searching or sorting on them is refused
  too, through any path (`user_ids.member_status`, `child_ids.membership_ids`)
  and on user accounts as well as contacts: Odoo refuses to read a restricted
  field but would let a search such as « has a membership » through, and a
  search must not list the members that reading hides.
* Categories and memberships are scoped per company by global record rules.
* The office fields of a membership (the decision note, the withdrawal
  reason, the last reminder sent, the import source and reference,
  the payment reference, the notes) are restricted to agents at field level,
  so they stay out of reach of the read access that `bf_membership_portal`
  gives members to their own records.
* Some of them are written by the module's actions only, never by hand, at
  creation or afterwards: who decided and when, the withdrawal date and
  reason, the reminder stage, the import source and reference, the renewal
  link. The decision note, the payment reference and the notes are typed by
  agents.
* Changes leave their trace in the membership's history: state, payment,
  period, amount, member, category, company, decision note and payment
  reference are tracked, and a change to the free notes logs who changed
  them (without copying their content). A delegate's person, role, vote and
  dates are tracked on the delegate; removing a delegate, or correcting a
  member number, is logged on the organisation's or the member's most recent
  membership. The context keys that silence Odoo's
  tracking (`tracking_disable`, `mail_notrack`, `mail_create_nolog`) are
  ignored unless the superuser itself acts (the scheduled task, the
  installation). The module's actions, which write as superuser on a person's
  behalf after checking their rights, keep the trace too.

## What it does not do

* **It does not invoice or issue receipts** (`bf_membership_account`).
* **It publishes nothing** (`bf_membership_portal`).
* **It does not hold general meetings** (`bf_membership_assembly`).
* **It does not prorate.** A membership taken mid-year costs the category's
  fee; the amount can be changed on the membership.
* **The member status on a contact ignores companies.** Categories and
  memberships are per company, but the status shown on a contact is computed
  from all its memberships: in a database that holds several unrelated
  associations, a member of one shows as in good standing in the other. The
  « Registre », the « Liste des membres » and the portal directory use the
  memberships of the user's or the website's company instead.
* **Delegates are shared by every company.** They live on the organisation's
  contact, not on a membership: in a database that holds several unrelated
  associations, an agent of one can see and change the delegates of a member
  of the other.

## Known limits

What the module does not close, on purpose or by Odoo's design:

* **Inferences by contact managers.** A user who manages contacts without a
  Members role can still infer membership: a merge refused where another
  succeeds, a contact archived instead of deleted (it shows under the
  « Archived » filter). Give contact-management rights sparingly.
* **A message's recipients.** Whoever receives a message posted on a
  membership sees, as with any Odoo message, who else it notified.
* **A held contact keeps its nature.** A contact that holds a membership, a
  delegation or a voter line cannot switch between person and organisation,
  not even by being merged into a contact of the other nature; create a new
  contact instead. A merge also replays the category and overlap rules on the
  memberships it moves, and keeps the consents of the contact that held the
  membership, not those of a duplicate.
* **Several associations in one database.** The status shown on a contact,
  and the status and dates columns of the « Registre », are computed from all
  companies; delegates are shared by every company. The « Registre » lists,
  the « Liste des membres » prints and the portal directory shows the
  memberships of the user's or the website's company only. Consents and member
  numbers are written only by someone who holds every company where the
  contact has a membership.
* **The member-number sequence** is readable by any employee, as Odoo's
  sequences are: it gives the count of numbered members, not who they are.
* **Consent proof needs a membership.** A consent set on a contact that holds
  no membership yet is not logged anywhere.

## Requirements

Odoo 18 Community. `depends`: `contacts`, `mail`, `bf_onboarding_base` (the
email layout used by the templates of this module and its add-ons). No
external Python dependency.

## Installation and configuration

1. Install `bf_membership`; it adds the « Membres » application.
2. Give staff the « Agent » or « Responsable » role (section « Membres » of the
   user form).
3. « Configuration > Catégories »: create the categories (regular, supporting,
   honorary, family, organisation...), each with its fee, period, voting
   right, admission rule and grace period.
4. « Configuration > Réglages » (administration rights): turn on renewal
   preparation and reminders if you want them, and set the reminder lead
   times.
5. Import your existing list, or create memberships by hand.

## Tests

The tests cover periods and fiscal-year edges, the two states and the state
changed only through actions (context defaults included), the member and
category frozen on history, the sequence skipping taken numbers, the deletion and
refusal guards, the first-good-standing member number, contact merges (one
number, consents never copied), status fields that cannot be written, grace
and former status, delegates and the one-vote rule, the daily pass with
everything off by default, late reminders skipped, import matching, conflicts
and preview, and access per role and per company, including the contact
fields hidden from employees without a Members role and refused in searches
and sorts (dotted paths and user accounts included), office fields written by
actions only, the tracking that a context key cannot silence, a reversed
payment that keeps the member on the register, and the status within one
company.

```bash
odoo -d <database> -u bf_membership --test-enable \
     --test-tags /bf_membership --stop-after-init
```

## Changelog

- **18.0.1.0.4**: first public release.

## License

Business Source License 1.1. The manifest says `Other proprietary` because the
manifest schema has no BUSL value; the `LICENSE` file governs.

* Licensor: Les services de consultation Blue Fox, Inc.
* You may use the module in production for your own internal business
  operations. Providing it to third parties as a product or service (hosted,
  managed or resold) requires a separate written agreement with the Licensor.
* Each version converts to LGPL-3.0-or-later at its Change Date; for this
  version, `LICENSE` sets 2030-10-01.

See `LICENSE` for the full terms.
