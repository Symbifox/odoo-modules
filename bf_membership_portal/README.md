# Membership: portal, online signup and directory (`bf_membership_portal`)

The membership register lives in the office. A member wants three things
without phoning: to know whether they are in good standing, to renew, and to
show their card. Someone who is not a member yet wants to join. And the
organisation sometimes wants a members directory, without publishing anyone
against their will.

This add-on opens those doors, and only those.

The pages, the acknowledgement email and the card are in French; the labels
below are quoted as they appear on screen.

## What it adds

| Route | For whom | What |
|---|---|---|
| `/my/membership` | the logged-in member | status, member number, period, history, invoices, consents, « Renouveler » (renew), membership card |
| `/my/membership/consent` (POST) | the logged-in member | saves their consents (directory, notices by email) |
| `/my/membership/renew` (POST) | the logged-in member | prepares their renewal and its invoice, then leads to the payment |
| `/my/membership/card` | a member in good standing | the membership card as a PDF, credit-card size: organisation, name, number, category, in good standing until (or lifetime) |
| `/membres/adhesion` | anyone | the public signup form (GET) and its submission (POST), for the categories ticked « Adhésion en ligne » |
| `/membres/adhesion/merci` | anyone | the thank-you page, the same for every request; the « Payer en ligne » button only for the session that sent a payable request |
| `/membres/adhesion/payer` (POST) | the session that sent the request | creates the invoice on the click, then leads to its online payment |
| `/membres/repertoire` | depends on the setting | the company's members directory: name and city of the members who agreed to appear |

The portal home (`/my`) gains a « Mon adhésion » entry as soon as there is a
membership to show. In the back office, a membership from the form gets a
« Formulaire public » flag, the contact the form created, an « À rapprocher »
(to reconcile) flag with its filter and ribbon, and two buttons to resolve it:
« Rattacher au contact existant » (attach to existing contact) and
« Rapprochée » (checked, nothing to attach).

The module does not create portal accounts: give a member's contact portal
access with Odoo's standard portal access wizard.

## Seven decisions, and why

### 1. A member is recognised by their login, never by their email

The portal follows the contact of the logged-in user
(`request.env.user.partner_id`). An email address links nothing: people change
it themselves in the portal, and two contacts can share one. No route takes a
membership or contact identifier in its address: there is nothing to guess.

### 2. A record rule, and fields that stay in the office

A portal user **reads** their own memberships, and those of the organisations
they currently represent as a delegate or substitute; they write nothing. The
rule also covers RPC calls, which read field by field: the office fields
(notes, the reason for a decision or a withdrawal, the import trace), the
fields of the public form (whether the request came from the form, the contact
it created, « À rapprocher ») and the invoice fields of `bf_membership_account`
are restricted to the Members roles, and the IP address of a public request to
managers. The portal reads what it needs of them with superuser rights, after
its own checks. Outside superuser, none of the public form's fields can be set
at creation, neither in the values nor through a `default_*` context key, so
nobody can forge a "public request" that the refusal, the daily clean-up or
the attach action would then process with superuser rights.

Consents are proven (Law 25: who, when, by which channel). Those given on the
public form are logged on the request when it is created: by the person who
sent it (the name and address they typed), when (the date of the line), « au
formulaire public » (on the public form), and both consents, ticked or not.
After that, the base module logs every change, by the team as well as by the
member in the portal, on the most recent membership, which only agents read,
with « au portail » (in the portal) when it came from the portal: one line per
change. Nothing appears on
the contact's thread, which every employee reads and which would tell them the
person is a member.

"Today" cannot be written in a record rule: Odoo caches each rule's evaluated
domain, so a date written into it would stay frozen at its first evaluation,
and a replaced delegate would still see the organisation weeks later. The rule
goes through `in_office`, a searchable field that computes the date at each
search.

### 3. The public form never touches an existing contact

Anyone can type anyone's email address into a public form. Attaching the
request to the contact that holds that email would let a stranger change a
member's address, consents or category, and an answer such as "you are already
a member" would tell them who is in the register (Québec Law 25). So the form:

* **always** creates a new contact, never modifies an existing one, and
  records on the request which contact it created (a field only the module
  can set);
* marks the request « À rapprocher » when a contact (archived ones included)
  already has that email;
* answers in exactly the same way whatever the register contains: the same
  validation messages, the same thank-you page, the same « Payer en ligne »
  button, the same acknowledgement email. It **never reveals whether an email
  is already known**.

### 4. The team is told, and the sender gets a bare acknowledgement

Each request from the form:

* **notifies the membership managers** of the company, delivered in their
  inbox or by email according to each person's preference, and says when the
  email matched an existing contact. Without it, a request waits until someone
  thinks of opening the requests list. It is an **internal note** with a
  generic subject (« Nouvelle demande d'adhésion »): a member to whom the
  request is later attached never reads in the portal what a stranger typed,
  and the typed name never travels in an email subject;
* **sends a plain acknowledgement to the address that was typed**, to the
  bare address (no name in the « To » header). It opens with « Bonjour, » and
  repeats nothing typed in the form, not even the name: otherwise anyone could
  have the organisation's address send a text of their choosing to an address
  of their choosing. It names only the category and
  the organisation, is the same whether or not the address is known, says that
  no membership is confirmed without the organisation's decision or the
  payment of the fee, and tells a person who sent nothing that they can ignore
  it.

### 5. Pay now, without leaving invoices behind for robots

A posted invoice cannot be deleted, so the form never creates one by itself.
When the category admits automatically and has a fee, the thank-you page
offers « Payer en ligne » (pay online). **The invoice is created only when the
visitor clicks that button**, then the visitor is taken to the invoice's
portal page to pay with Odoo's online payment. A second click finds the
invoice the first one created. A category that admits by decision waits for
the decision, and nothing is invoiced.

The button belongs to the browser session that sent the request, never to the
page address, and only while the request is still carried by the contact the
form created. Once the request has been attached to an existing contact, or
that contact merged, the session can no longer pay it: the invoice would carry
the real person's name and address.

Four guards against automated submissions:

* Odoo's CSRF token;
* a honeypot field hidden from people (a filled honeypot creates nothing and
  shows the same thank-you page);
* a cap of five requests per hour per IP address;
* a daily scheduled action: the invoice created by the « Payer en ligne »
  click, still unpaid **14 days** after it was created, is cancelled and the
  request refused. An invoice that received a payment, even partial, one with
  an online payment in progress, or one the team issued on a public request,
  is left to the team, as is a request no longer carried by the form's
  contact.
  An online payment counts as in progress from the moment the transaction
  exists, still in draft, until it settles or fails. The same action erases
  the IP address of public requests after seven days. It sends nothing.

The IP address is visible to managers only.

⚠️ **Behind a reverse proxy, run Odoo with `proxy_mode = True`.** Otherwise
Odoo sees the proxy's address on every request, and the per-IP cap becomes a
global cap: five requests per hour for everyone.

### 6. Attach a request to the existing contact; do not merge

When a request is « À rapprocher », someone checks it and, if it belongs to a
person already in the register, clicks « Rattacher au contact existant »
(the wizard proposes the contact with the same email when there is exactly
one):

* the membership moves to the existing contact and is no longer « À
  rapprocher »;
* the unpaid invoice created by the « Payer en ligne » click is cancelled and
  detached from the membership; an invoice that received a payment, even
  partial, one with an online payment in progress, or one issued by the team,
  stays linked;
* the existing contact keeps its member number; if it has none, it takes the
  form contact's number, otherwise one comes at the first good standing;
* the form's contact, now without a membership, is **deleted**, or
  **archived** when something still points to it (an invoice, even cancelled,
  or a payment). It does not stay active with the real person's email. As a
  safeguard, a contact that has a login or still carries a membership is left
  untouched.

If the existing contact already has a live membership for the same period,
the base module refuses the overlap: refuse the duplicate request instead.
The refusal itself cancels the unpaid invoice of its « Payer en ligne » click,
if there is one.

**Do not merge** the form's contact into the existing one. A form contact is
never the **kept** contact of a merge: its data and consents are what a
stranger typed, so that merge is always refused. Odoo's merge wizard rewrites
in SQL everything that points to the merged contact, so an
invoice issued to the name typed in the form would move to the real person's
name and address, readable through its payment link. Merging a form contact
that carries an invoice is therefore **refused**, with a message pointing to
« Rattacher au contact existant ». With a Members role and without an
invoice, merging the form's contact into the existing one goes through, but
the request can then no longer be paid from the form.

Without a Members role, this module's messages, which name the form and the
invoice, never appear. The module extends the base module's
`_membership_held` hook to the contacts the form created, even once their
request has been attached: without the role, such a contact cannot be merged
(the base module's neutral refusal, « avec vos droits », with your rights,
comes before this module's checks) nor deleted (the base module archives it
instead, and the request notes it). A contact linked to an active user keeps
Odoo's native refusal, the same for everyone.

⚠️ What remains visible without the role: someone who manages contacts can
still infer that a contact is tied to the association, without reading why. A
merge refused where another goes through, or a contact archived instead of
deleted (visible under the « Archivés » filter), gives it away. The refusal
and the archiving do not say why; they do not hide that they happen.

A cancelled form invoice gets a new access token, so the payment link the
visitor received stops working. A form contact that carries an invoice, even
a cancelled one, cannot be deleted: accounting keeps the invoice with its
contact. With a Members role, a message says to archive it; without the role,
the base module archives it instead (see above).

**Refusing a request from the form** works even after its session clicked
« Payer en ligne »: the unpaid invoice is first cancelled and detached, with
superuser rights, so an agent without invoicing rights can refuse the
duplicate. Only that request's own invoice is cancelled this way (the one its
« Payer en ligne » click created, issued to the form's contact for the
category's product), never another one, nor one issued by the team. An
online payment in progress is always spared: the invoice is not cancelled
under the person who is paying, so the request cannot be refused until that
payment settles or fails.

### 7. Renewing twice makes one renewal

« Renouveler » prepares the renewal if it is missing, its invoice if one is
needed, then leads to the invoice's payment page (Odoo's native online
payment). A free renewal is simply confirmed. A second click finds what the
first one created, and two simultaneous clicks are queued on the database row
of the membership being renewed, so they still make one renewal and one
invoice.

For a person attached to a company, whom `bf_membership_account` never
invoices in Odoo, « Renouveler » prepares the renewal and the page says that
the organisation will contact them about the payment.

Renewal is offered when the end date is within the category's renewal lead
time, or during the grace period. A delegate does not renew their
organisation: they see its membership, read-only. The renewal handled is
always the logged-in person's own: a live renewal that belongs to someone else
(attached to their membership by mistake) is not offered (no « Renouveler »
button), and the renewal route still refuses it, with no invoice and no
redirection to another person's invoice. Renewal is not offered past
the grace period; a former member joins again through the public form.

## The directory is closed by default

Québec Law 25, s. 9.1: the most protective settings by default. Closed, the
directory answers 404, as if it did not exist. It can be opened to logged-in
members (employees, and members in good standing or in grace, directly or
through an organisation they represent; anonymous visitors are sent to the
login page) or to the public. Open, it lists only the members **in good
standing or in grace** of the company who **have consented**, and only their
**name and city**: the records never reach the template, only those two
values.

The status is read in the directory's company only (`_member_status_in`),
never the all-companies status: in a database that holds several companies, a
former member of one company does not appear in its directory because they
are in good standing in another, and a person in good standing in one company
does not open another company's « Membres connectés » directory. The
membership card is read the same way: a person in good standing only in
another company does not get it from this company's site.

In « Membres connectés » mode, every employee (internal user) also reads the
directory, with or without a Members role: it shows only the names and cities
of those who consented.

Members change their own consents on `/my/membership` (« Mes choix »). The
base module logs each change on the most recent membership, which only agents
read, marked « au portail »: one line, and nothing on the contact's thread.

## Off by default

* The members directory: closed.
* The public signup form: no category is offered until one is ticked
  « Adhésion en ligne »; with none, `/membres/adhesion` answers 404.
* Both consents on the form: unchecked.

## Settings

* « Membres > Configuration > Réglages », block « Portail et répertoire »:
  the directory, « Fermé », « Membres connectés » or « Public ». This is Odoo's
  Settings: opening the directory is an administrator's decision.
* On each category: « Adhésion en ligne ».
* To pay online: an active payment provider, and online invoice payment
  enabled in Invoicing.
* Behind a reverse proxy: `proxy_mode = True` in the Odoo configuration.
* ⚠️ Odoo 18 gives the « Création de contacts » right
  (`base.group_partner_manager`) to every new internal user through its
  default user template, so a membership agent can create contacts unless an
  administrator removes that right. Attaching a request always picks an
  existing contact.

## Known limitations

* **What remains visible without the Members role**: someone who manages
  contacts can still infer that a contact is tied to the association, without
  reading why: a merge refused where another goes through, a contact archived
  instead of deleted (visible under the « Archivés » filter).
* **Behind a reverse proxy**, without `proxy_mode = True`, the cap of five
  requests per hour per IP address becomes a global cap.
* **A form contact that carries an invoice**, even a cancelled one, cannot be
  deleted: it is archived.
* **Online payment from the form** is offered only to the session that sent
  the request, while the request is carried by the form's contact and its fee
  is the category's. A request that was attached, or whose fee the team
  changed, is settled with the organisation; the page says so.
* **A person attached to a company** gets no invoice at renewal: the renewal
  is prepared, and the organisation settles the payment with them.
* **The invoice of an attached request**, issued in the name typed in the
  form, does not appear in the member's portal: the member asks the
  organisation for it.
* **The « Membres connectés » directory** is readable by every employee
  (internal user), with or without a Members role.
* **In a database with several companies**, the status, the card, the
  directory and the renewal are read in the site's company only.

## What it does not do

* **It sends no other email.** Apart from the acknowledgement and the
  managers' notification above, nothing is sent, the daily clean-up included:
  the member finds the invoice in the portal and pays it there.
* **It does not issue tax receipts in the portal.** Issuing a receipt counts
  it (the original, then duplicates), so it stays an office action in
  `bf_membership_account`.
* **It does not decide reconciliation.** A request marked « À rapprocher » is
  checked and attached by a person.

## Security and access

* Portal users: read-only access to their own memberships and to those of the
  organisations they represent today, through a record rule. The member's
  page shows an invoice only for a membership of the logged-in person (or of
  the organisation they represent), and only when the invoice is in that
  owner's name (same commercial partner), checked against the logged-in user
  rather than the membership's contact alone: a paid invoice that a request
  kept after being attached, issued in the name typed in the form, does not
  appear there.
* An employee without a Members role who is also a member sees their own
  memberships in the portal page, read with an explicit domain on their own
  contact.
* The signup form writes, with superuser rights, only after the CSRF check,
  the honeypot and the IP cap. The directory page receives names and cities,
  never records.
* « Rattacher au contact existant » requires the right to write the
  membership (agents and managers).

## Requirements

Odoo 18 Community. `depends`: `bf_membership_account`, `portal`,
`account_payment`. No external Python dependency.

## Installation and configuration

1. Install `bf_membership_portal` (it pulls in `bf_membership_account` and
   `bf_membership`).
2. Configure a payment provider and online invoice payment if members are to
   pay online.
3. If Odoo runs behind a reverse proxy, set `proxy_mode = True`.
4. Tick « Adhésion en ligne » on the categories open to the public, and link
   `/membres/adhesion` from your site.
5. Have an administrator open the directory only if you want one, and only to
   the audience you choose.
6. Grant portal access to the members who should see `/my/membership`.

## Tests

The tests include access played in the intended role (member, delegate in
office, former delegate, person without a membership) and pages played over
HTTP with the real CSRF token: one renewal and one invoice for two clicks, no
invoice before « Payer en ligne », another session unable to pay, the same
acknowledgement and the same pay button whether the email is known or not, an
acknowledgement that repeats nothing typed, an existing contact never touched,
the honeypot, the IP cap and its erasure, the 14-day clean-up (and the team's
invoice it spares), the refused merge, the neutral refusal without the role
and the form contact never kept by a merge, the form contact archived
silently without the role, attaching to an existing
contact (invoice, online payment in progress, member number, form contact
deleted or archived, a contact with a login never removed), refusal never
cancelling another invoice, an agent refusing a request whose session clicked
« Payer en ligne », an agent unable to forge a public request, a renewal
forged for another person refused, the invoice hidden from the member's page
when it is in the typed name, the new access token, the directory's consent
filter, and the directory read in its own company (two companies), the
renewal not offered when it belongs to someone else, one consent line per
change, the consents of the public form logged on the request, the
category's fee required by « Renouveler » and « Payer en ligne », the card
read in the site's company, and the neutral refusal for a form contact whose
request was attached.

```bash
odoo -d <database> -u bf_membership_portal --test-enable \
     --test-tags /bf_membership_portal --stop-after-init
```

## Changelog

- **18.0.1.2.1**: first public release.

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
