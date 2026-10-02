# Membership: invoicing and tax receipts (`bf_membership_account`)

The membership register (`bf_membership`) records that a fee is paid, however
it was paid. When the organisation's accounting lives in Odoo, the question
changes: the fee is an invoice, and the invoice is what knows whether it is
paid.

This add-on makes the invoice the source of the payment, and adds what a
registered charity owes its members: the official receipt for the eligible
part of their membership fee.

The interface and the receipt are in French; the labels below are quoted as
they appear on screen.

## What it adds

| Where | What |
|---|---|
| Category (`bf.membership.type`) | The fee product, created at the first invoice; whether the category gives a tax receipt, the value of the advantage a member receives and its description |
| Membership (`bf.membership`) | The invoice, the « Facturer » (invoice) and « Reçu fiscal » (tax receipt) buttons, the amount eligible for a receipt, the member's name and address frozen at payment for the receipt (« Nom au reçu », « Adresse au reçu ») |
| Receipt (`bf.membership.receipt`) | A frozen copy of each receipt issued: serial number, deliveries, cancellation and replacement |
| Company (`res.company`) | The charity registration number, the authorised signer and their title, the signature image (administrators and membership managers only), the place of issue |

The receipts are listed under « Membres > Reçus fiscaux ».

## Eight decisions, and why

### 1. The invoice is posted at once, and only the module links it

Nothing is decided on a membership invoice: the product comes from the
category and the amount from the membership. A draft has no number, does not
appear in the portal and **cannot be paid online** (Odoo only offers payment
on a posted invoice), and the online renewal of `bf_membership_portal` needs
exactly that. Posted, the invoice is a receivable, so the list of fees to
collect is right from today. A mistake is corrected with a credit note, like
any invoice.

The agent who invoices does not need invoicing rights: the person who
welcomes members is not necessarily the one who keeps the books. The invoice
is created with superuser rights **after** the agent's right to write the
membership has been checked, and the agent reads its number and payment state
on the membership. The « Facture » smart button that opens the invoice is
shown to invoicing users only. An agent invoices **only the category's fee**:
when the amount on the membership differs from it, the invoice is issued by a
membership manager or an invoicing user, so that an agent without accounting
rights cannot post customer invoices of any amount. The same guard applies to
the automatic paths that the portal runs with superuser rights (the member's
« Renouveler », the public form's « Payer en ligne »): when the amount is not
the category's fee, or for a person attached to a company, no invoice is
posted automatically, and the page says to contact the organisation.

⚠️ Accounting users read membership invoices like any invoice: the member's
name, the category (in the label) and, in the origin, the member number when
there is one. Whoever can read customer invoices therefore learns who pays a
membership fee.

The invoice goes to the member, labelled « Cotisation <category> <period> »,
with the period as its origin, and the member number too when the member
already has one (it comes with the first settled membership, so often after
the first invoice); a note on the membership links to it. The link between a membership and its invoice is set only by the
module (« Facturer », or the online payment of the portal): writing it any
other way, through the interface or an RPC call, is refused, so nobody can
point a membership at another customer's invoice.

### 2. Payment follows the invoice, through the one path that sees everything

An invoice's `payment_state` is a **stored computed field**: Odoo writes it to
the database when it flushes, without ever going through `write()`. An
override of `write()` sees neither the payment nor the credit note, and
`_invoice_paid_hook()` sees the move to "paid" but never the way back. The
add-on therefore hooks the computation itself
(`account.move._compute_payment_state`): it lets Odoo compute, then writes
the membership through its own `write()`, which carries the base module's
state transition.

| The invoice | The membership |
|---|---|
| paid, or in payment | paid, « En règle », dated with the latest reconciled payment |
| partially paid | to pay |
| reversed by a full credit note | to pay |
| refunded by a credit note (Odoo keeps the invoice "paid") | to pay |
| reset to draft, or cancelled | to pay |
| payment cancelled (a bounced cheque) | to pay |

Payments and credit notes made through Odoo's own wizards and the online
payment of the portal all take this path.

A request still awaiting a decision (« Demande », in a category admitted by
decision) that is invoiced and then paid stays « Demande »: paying is not a
decision. Once accepted, it becomes « En règle ».

### 3. One truth about the payment

While a posted invoice carries the fee, marking the membership paid by hand
or exempting it is **refused**: two truths, and whichever moved next would
silently erase the other. On the form, the payment state, source and date are
read-only only while the invoice is authoritative; the payment reference
stays editable. To
take a membership off its invoice, reverse the invoice with a credit note; a
payment received elsewhere can then be recorded, and its source is no longer
"invoice".

The payment source itself is frozen while a posted invoice carries the fee,
once the fee is settled, and while an issued receipt has not been cancelled: who may issue and
cancel a receipt depends on how the fee was paid, and the source must not be
switched to borrow wider rights.

### 4. What the receipt reads is frozen

The receipt reads the fee and the payment date. Both are **frozen** as soon
as a posted invoice carries the fee or the fee is settled (paid or exempt):
any change is refused, including one that would come from changing the
category. A mistake is corrected with a credit note on the invoice; a payment
recorded by hand is first put back to "to pay".

A membership carried by a posted invoice (neither cancelled nor reversed) can
be neither refused nor deleted: cancel the invoice, or reverse it with a
credit note, first. Otherwise the refusal would leave an open receivable. It
also keeps its member, category and company, like a paid membership in the
base module: otherwise paying one person's invoice would make someone else a
member, and the receipt would go out in that other name.

### 5. Who is invoiced, and who is not

The invoice is **always in the name of the member itself**: the member
organisation, or the person. Never a child contact (an organisation's billing
address): such a contact can be attached to another company with a simple
write, and that company's portal would then see the invoice and its PDF.

A **person attached to a company** (an employee recorded under their
employer) **is not invoiced in Odoo**: Odoo puts every invoice on the
commercial partner, so the receivable would be the company's, and anyone from
that company with portal access would read the fee, and with it their
colleague's membership (Québec Law 25). « Facturer » refuses and says why:
record the payment another way (cheque, transfer, online payment outside an
invoice), or detach the contact from the company first.

For the same reason, a member whose membership is carried by a posted fee
invoice keeps its place in the contact hierarchy: attaching a person to a
company, giving an organisation a parent company (through the interface or an
RPC call), or changing a parent's `is_company` is refused while the invoice
carries the fee. Odoo opens an invoice in the portal of anyone whose
commercial partner is a parent of the customer. Without a Members role, the refusal is
neutral (« Ce changement n'est pas permis avec vos droits », this change is
not allowed with your rights); with the role, it says why. Reverse the invoice
with a credit note, or cancel it, first. A contact created directly under a
company has no invoice to move: it is not invoiced in Odoo.

### 6. The product is created without tax

A non-profit's or a charity's fee that gives only the right to vote and to
receive notices is usually exempt; a fee that gives significant benefits may
be taxable. The module does not decide. ⚠️ **If your fees are taxable, set the
taxes on the category's product before the first invoice.**

### 7. A receipt is a frozen copy, and issuing it means rendering the PDF

Everything a receipt shows is copied when it is prepared: a member who moves
does not change last year's receipt, and a duplicate says exactly what the
original said. The one exception is the company logo, which is read when the
PDF is rendered, not copied. The delivery count is kept when the PDF is rendered, not on the
button: a receipt also prints from the Print menu, and every path to the PDF
goes through the rendering. Only the PDF counts: the HTML preview of a receipt
is not a delivery, does not use up the original, and is marked « APERÇU »
(preview) so that nobody prints it as a receipt.

### 8. The name and address on the receipt are frozen at payment

The member's name and address that the receipt prints are **frozen when the
fee becomes paid** (« Nom au reçu », « Adresse au reçu », restricted to the
Members roles and tracked on the membership), with superuser rights, and are
not read from the contact when the receipt is issued. Otherwise an agent could
rename the contact, issue a receipt in a third party's name, then put the name
back, without a trace. They are cleared if the fee stops being paid.

The address is frozen only when it is complete (street and city). If it was
missing at payment, the receipt is refused until the address is completed and
a membership manager clicks « Mettre à jour le nom et l'adresse au reçu »
(update the name and address on the receipt), which takes the contact as it
stands that day; the button is reserved to managers and the change is
tracked. The frozen address keeps no blank line and no trailing space.

## The membership-fee receipt

For a **registered** charity only; a non-profit that is not registered with
the Canada Revenue Agency issues no receipt.

**The amount and the date received.** When the payment source is « Facture »
(the code requires `payment_source == "invoice"`) and a paid invoice carries
the fee, the receipt certifies the **cash received**: the money received
through the payment entries reconciled with the invoice, bounded by the
reconciliation, and it is issued only when that cash equals the fee. Only
these count as money received: an inbound payment entry, on a bank or cash
journal, that carries nothing but the receivable and one of the payment's
liquidity accounts (including the outstanding-receipts account, which the
accounting treats as received while awaiting bank reconciliation); a bank
statement line reconciled directly; or a payment without a journal entry for
this invoice alone. A write-off (« marked as fully paid »), an early payment
discount, an exchange difference, a reconciled credit note, or any customer
credit note to the same member that follows the invoice, even entered
separately and without the fee product, gives
**no** receipt, with the refusal « le paiement ne couvre pas la cotisation en
argent reçu » (the payment does not cover the fee in money received). The
receipt's date is that of the reconciled entries, never the amount or date
typed on the membership. The invoice must be in the member's own name and
carry the category's product; if not, no receipt is issued. When no invoice carries the fee, the receipt uses the
amount and payment date recorded on the membership, and then only a
**membership manager or an invoicing user** may issue or cancel it: a payment
recorded by hand has no accounting entry behind it, so someone who answers for
it checks it. Which case applies is decided on the actual invoice: the source
« Facture » AND a posted, paid invoice carrying the fee. A source switched to
« Facture » with no paid invoice behind it changes nothing. Printing a receipt
already issued again (a duplicate) stays open to agents: it reads the frozen
copy under the same number, it is not a new issue.

**What the receipt certifies**: the membership fee of an eligible category,
received in full in money, on the date it was received, and its eligible part
under CSP-M05. Nothing else. The receipt requires an invoice
for the fee alone: exactly **one** product line, of the category's product,
with **quantity 1**, whose subtotal before tax equals **the membership's
fee**, in the company's currency. An invoice that was reset to draft and then
changed (a fundraising-dinner ticket added, a second line of the fee product,
a quantity of 2, a changed price) gives **no** receipt, and the refusal says
so. Neither does a fee invoice that carries taxes. Anything else is invoiced
separately.

**The eligible amount** follows CRA policy CSP-M05 on membership fees:

* no advantage, or a negligible one (at most the lesser of $75 and 10 % of the
  fee): the full fee, and the advantage is not deducted;
* an advantage worth more than 80 % of the fee: no receipt;
* in between: the fee minus the advantage (a split receipt).

Each bound is inclusive on the member's side: an advantage equal to the
negligible threshold is disregarded, and one equal to 80 % still gives a
receipt.

**What must be in place.** A receipt is refused, with the list of everything
missing, until the organisation has a complete address in Canada (street,
city, postal code, country), a registration number and a place of issue (the
setting, or else the company's city). It also needs an authorised signer in
the settings, a payment date, and the member's name and complete address
frozen at payment (see decision 8).
It is issued only for a fee that is **paid** (not exempt), in an eligible
category, with an eligible amount above zero.

**The receipt** carries the elements of section 3501 of the Income Tax
Regulations as the module applies them: a statement that it is an official
receipt for income tax purposes; the charity's name and address; its
registration number; the receipt's serial number; the place of issue; the date
the fee was received and the date of issue; the member's name and address; the
amount of the fee; the value and description of the advantage; the eligible
amount; the name and signature of the authorised person; and the name and
website of the Canada Revenue Agency.

**The serial number** comes from a gap-free sequence per company that restarts
each year (`2026-00001`). Then:

* **issuing again** produces a **duplicate**, marked as such, under the same
  number. Every delivery (the original as well as each duplicate) redoes the
  receipt's full check and requires the same amount and the same date as the
  frozen receipt: after a bounced cheque followed by a new settlement (with a
  written-off difference, or in money on another date), the duplicate is
  refused, and the receipt is cancelled or replaced;
* **« Annuler ou remplacer »** (cancel or replace) requires the same rights
  as issuing the receipt. It cancels the receipt, which stays in the register
  marked cancelled, and by default issues a new receipt under its own number
  that names the receipt it replaces (Income Tax Regulations s. 3501(4) and
  (5), as the module applies them). Untick the replacement when the fee was
  refunded. The replacement is checked before the original is cancelled, so a
  refused replacement never leaves the member without a valid receipt;
* a receipt can be neither modified nor deleted, not even by a manager; the
  organisation keeps a copy of every receipt, cancelled or not.

If the invoice of a receipted fee stops being paid (reversed, payment
cancelled), a note says so on the membership and duplicates are refused: the
receipt has to be cancelled.

**The signature image** on the company is readable only by administrators and
membership managers, and editable only in Settings, by an administrator; the receipt copies it with superuser
rights when it is prepared. Without that restriction, Odoo's image route would
serve it to anyone who can read the company, and a downloaded signature is
enough to forge an official receipt. It is not a secret from agents, though:
each receipt keeps a frozen copy of the signature to print it, and the agents
who print receipts can read that copy.

⚠️ **The receipt template must be validated by the organisation's accountant
before the first receipt is issued.** Its wording follows the Regulations and
the CRA policy as they read in 2026; the exact form an organisation gives its
receipts (place of issue, signature, duplicate marking) remains its own
responsibility. The template is in French.

## The official invoice PDF

Without its official PDF, Odoo's portal shows an invoice as a pro forma, which
a member about to pay reads as "not a real invoice". Rendering the PDF while
invoicing would hold the person's request for several seconds, so a scheduled
action produces it right after each invoice (it is triggered by the invoice,
and also runs hourly as a safety net) for membership invoices from the last
30 days that lack one. It sends nothing, and it does not mark the invoice as
sent, so the accounting does not read as sent an invoice nobody sent.

## Settings

* « Membres > Configuration > Réglages », block « Reçus fiscaux de
  cotisation »: the registration number (format `123456789 RR 0001`), the
  authorised person and their title, the signature image, the place of issue
  (the company's city when empty). This is Odoo's Settings: the receipt
  configuration is set by an administrator.
* The company's own address, in the company form: street, city, postal code
  and country.
* On each category: « Reçu fiscal », the value of the advantage and its
  description (required when there is an advantage).

⚠️ Odoo 18 gives the « Création de contacts » right
(`base.group_partner_manager`) to every new internal user through its default
user template, so a membership agent can create and edit contacts. An
administrator who removes that right stops them from creating contacts; they
can then only enrol a person already in the address book. Renaming a contact
does not change the tax receipt, whose name and address are frozen at
payment.

The company fields are prefixed `membership_` so that this module can live in
the same database as the donation-receipt module `bf_receipt_ca` without
collisions. The two share no code.

## What the portal does not read

Members read their own memberships in the portal, and through RPC calls field
by field. The linked invoice, its state, its payment state and the payment
reference (which carries the invoice number) are restricted to the Members
roles: the invoice of a public request that was attached to a member carries
the name a stranger typed. The portal reads what it shows with superuser
rights, after its own checks.

## The trace is not erased

The context keys that would erase the trace of a change (`tracking_disable`,
`mail_notrack`, `mail_create_nolog`) are ignored on the membership, the
category and the tax receipt for anyone but the superuser acting for itself
(scheduled actions, installation). This includes the writes the module makes
with superuser rights on a person's behalf, such as linking the invoice or
cancelling a receipt: they keep their trace.

## Known limitations

* **An invoiced member can no longer be attached to an employer**, nor an
  invoiced organisation to a parent company, while a posted fee invoice
  carries the membership, even once paid: the invoice would open in the
  employer's portal. Reversing the invoice and then attaching the contact is
  not enough: Odoo also opens the reversed invoice and its credit note in the
  employer's portal. The safe way: create a **new contact** under the
  employer for the person in their employee role, and keep the member's
  contact **standalone**, with its invoices and receipts.
* **The followers of a fee invoice** are limited to the member itself and
  internal users: writing to someone else from the invoice's thread (an
  organisation's billing person, a delegate) emails them, but does not make
  them a follower. Odoo's portal rule would otherwise open the invoice in the
  portal of any follower's company.
* **An invoiced organisation is not merged** into a kept contact with other
  parents (a duplicate attached to another company): the invoice would move
  under those parents. Without a Members role, the base module's neutral
  refusal comes first; with the role, the refusal says why.
* **A fee invoiced with taxes gives no tax receipt**: the module does not
  separate a tax from a gift.
* **Payments awaiting bank reconciliation** (the outstanding-receipts
  account) count as money received, as Odoo treats them. A payment rejected
  later undoes the invoice's payment, and the note asks to cancel the receipt.
* **Any customer credit note to the same member** (same commercial partner),
  dated on or after the invoice, with or without the fee product, blocks the
  receipt, even if it concerns something else: a refused receipt is better
  than a false one. The module does not issue
  that receipt; the situation is checked with the accounting.
* **A payment without a journal entry that covers several invoices** cannot
  be split: no receipt.

## What it does not do

* **It sends nothing.** Neither the invoice nor the receipt is emailed;
  sending them is up to the organisation.
* **It does not depend on fundraising.** Donation receipts are a separate
  module, under a different licence.
* **It does not invoice the renewals prepared by the daily pass.** A renewal
  is invoiced with the button, or from the portal when the member clicks
  « Renouveler » (`bf_membership_portal`).

## Security and access

* The roles are those of `bf_membership`. An agent can invoice the category's
  fee, and can issue, duplicate, cancel and replace receipts for fees paid by
  an actual paid invoice. A different amount is invoiced, and a receipt for a
  fee recorded by hand is issued or cancelled, by a membership manager or an
  invoicing user; printing an already issued receipt again stays open to
  agents.
* A person attached to a company is never invoiced in Odoo, and a person
  whose fee invoice is posted cannot be attached to one afterwards (see
  above).
* Nobody creates or edits a receipt by hand: access is read-only, and every
  change goes through the code.
* An employee without a Members role can neither invoice nor read receipts.
* Receipts carry a member's name and address: a global record rule scopes
  them per company.
* The invoice fields, the payment reference and the name and address frozen
  for the receipt are restricted to the Members roles.

## Requirements

Odoo 18 Community. `depends`: `bf_membership`, `account`. No external Python
dependency.

## Installation and configuration

1. Install `bf_membership_account` (it pulls in `bf_membership`).
2. If your fees are taxable, set « Article de cotisation » on each category
   to a service product that carries the right taxes before the first
   invoice; otherwise the product created at the first invoice has none.
3. For receipts: complete the company's address in Canada, have an
   administrator fill in the « Reçus fiscaux de cotisation » settings, tick
   « Reçu fiscal » on the eligible categories with the value and description
   of their advantage, and have the receipt template validated by your
   accountant.

## Tests

The tests are played in the intended role: an agent without invoicing rights,
an accounting user, a membership manager, a plain employee, the public and
portal users. Payments and credit notes go through Odoo's real wizards
(`account.payment.register`, `account.move.reversal`). They cover the CSP-M05
bounds, every mention on the receipt, the amount and date taken from the
invoice (partial refunds included), the refusal when the member or the
category no longer matches the invoice, the issuer rule for payments recorded
by hand (issuing and cancelling, decided on the real invoice), the agent
limited to the category's fee (the portal's automatic paths are tested in
`bf_membership_portal`), the person attached to a company who is not
invoiced, the frozen payment source and identity, the organisation's Canadian
address, the name and address frozen at payment, the HTML preview, duplicates
and replacements, gap-free numbering per company, the frozen amount and
payment date, the invoice link, the protected signature, a colleague on the
portal who sees no membership invoice (including after an attempt to attach
an invoiced person to their company), the single fee line the receipt
requires, the cash received it certifies (a written-off difference, an early
payment discount, a credit note, one entered separately, a partial payment),
the invoice in the member's own name and the parent company refused to an
invoiced organisation, the trace kept on the membership,
receipt and category, and each row of the payment table above.

```bash
odoo -d <database> -u bf_membership_account --test-enable \
     --test-tags /bf_membership_account --stop-after-init
```

## Changelog

- **18.0.1.0.2**: first public release.

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
