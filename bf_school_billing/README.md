# Symbifox École: family billing (`bf_school_billing`)

Billing of the families of a Québec private school, on top of the contract for
educational services (`bf_school_contract`): the price comes from a catalogue, the
contract says who pays, and each payer receives one invoice a year that follows the
contract's instalments.

## Features

- **Fee catalogue** per school year: educational services (they make the contract's
  tuition), accessory services (added to every contract they apply to) and optional
  services (added when the family chooses them), by level and by profile (particular
  programme, see `bf_school_core`). Each fee is marked as child care for the Relevé 24,
  not child care, or reduced contribution (prescribed fees, never on the Relevé 24).
- **Contracts prepared from the catalogue**: one button on a school year prepares a draft
  contract for every enrolled student who has none, with the level and profile of the
  enrolment, the registration fee of the student's re-enrolment, the eligibility fee of
  the admission, and the number of instalments set on the school. A student without a
  level, or a contract the Act refuses (a fee above its cap), is reported and does not
  stop the others. A draft can be priced again after a change of level or profile; the
  optional services the family chose stay.
- **Sibling discount** by rank in the family: among the students enrolled this year who
  share a payer, by birth date (1 is the eldest), from a rank set on the school (for
  example 15 % from the third child), on the fees marked for it.
- **Payers of a contract**: family shares in percent (shared custody, 50/50 or otherwise),
  and bursaries or third parties paying a fixed amount first. By default, the adults
  ticked « Pays » on the student's family links, in equal shares.
- **One invoice per payer**, once the contract is signed and the office has checked who
  pays: the last signature leaves the office an activity, the office clicks « Bill the
  payers ». The due dates are the contract's instalments (a payment term made for the
  contract, archived), the admission fee first when it is not already invoiced by the
  re-enrolment, nothing else due before the services begin (Act s. 70). The lines carry
  the student's name and say which part is child care; a bursary or third party gets a
  single line, its share. Educational services are exempt: no tax. A registration fee
  invoiced by the re-enrolment but missing from the contract's price stops the billing.
- **Sending**: the invoices and credit notes are emailed to their payers with their
  official PDF by a triggered cron, from the school, not from the person who clicked; a
  failure is tried again at the next run; a payer without an email address is told to
  the office (a note and an activity), never marked « sent ». Nobody follows an invoice
  but its payer: the other parent never receives it nor sees it on the portal.
- **Amount paid** on the contract counts money: payments and bank lines (net of their
  write-off), and a credit note of another invoice applied here; less what was paid back.
  A credit note of the same invoice, a bad-debt write-off, are not payments. A contract
  billed outside Symbifox keeps the amount typed by hand.
- **Termination** (Act s. 71-74): once the contract computes what the school may keep,
  each payer is credited in proportion for the rest and the credit note is reconciled with
  the invoice; what a payer paid beyond is the refund due within ten days, shown on the
  contract and left as an activity, with its deadline, for the person who recorded the
  termination. Refused while an invoice of the contract is a draft.
- **Contract invoices are corrected by credit notes**: they are not reset to draft, and an
  invoice of a terminated contract is not confirmed any more.
- **Portal**: a payer finds an « Invoices and payments » button on the school page; the
  invoices are Odoo's own, paid online by instalment with any payment provider (Stripe,
  …) or recorded by the office.

## Security

- The catalogue, the sibling discounts and the payers belong to the school office
  (Administration). Teachers have no access right at all.
- The office bills without the Invoicing group (it opens the employees' bank accounts):
  the buttons check the right on the contract, then create the invoices in sudo; the
  office reads the number, the amount paid and the next due date of each payer's
  invoice on the contract, never the accounting.
- What the billing sets (the invoice of a payer, the amount paid of a billed contract, the
  registration fee deducted, the rank in the family) is refused over RPC, and a contract
  is not created already signed; the payers of a billed contract are frozen: a change goes
  through a credit note in the accounting.

## Limits

- Moneris is not supported (Odoo 18 has only third-party modules under OPL-1); the
  invoices are paid with Odoo's providers.
- No late fees: the contract would have to provide for them.
- The share of a bursary applies to the whole price, in proportion: a bursary that
  covers only the tuition is not modelled.
- An invoice issued after the services began has its past instalments due at once.
