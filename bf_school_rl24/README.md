# Symbifox École: Relevé 24 (`bf_school_rl24`)

What each payer paid for each child's child care, by calendar year, for the Relevé 24
(Québec child-care expenses slip), from the contract invoices of `bf_school_billing`.

## What counts

The school decides which of its fees are child care in the sense of the Taxation Act
(s. 1029.8.67 and 1029.8.68), on the fee catalogue. Revenu Québec's interpretation
letters guide the choice:

- letter 17-038083-001 (2017, to the FEEP): pre-K and kindergarten fees of a private
  school can count in full; the school splits its fees itself and reports the total
  paid by each parent, whatever the child's age, without threshold;
- letter 25-070654-001 (2025): at compulsory school age, fees are teaching, except a real
  child-care service (optional, attendance taken, the student stays under supervision);
  lunch supervision, homework help, regular sports and meals billed apart are not child
  care; the parental contribution set under s. 84 of the Act respecting private
  education is a prescribed fee.

## Features

- **Computation** for a company and a calendar year: on each contract invoice, the money
  the school kept (payments, less what it paid back after a termination) settles the
  contract's instalments in the order of the services, a termination credit note taking
  off the last ones first. What settles the services of the year counts, at their
  child-care share; the admission fee never does, nor, after a termination, the penalty
  the school keeps beyond the months provided (Act s. 73). A payment counts for the year
  of the services it pays, whenever it was made (« pour des services rendus dans cette
  année »).
- A company, the share of a bursary or a third party, and meals billed apart never count.
- **Statements** to review, then issued by the office; only the computation creates them.
  An issued statement is never rewritten nor deleted: when the payments change, it is
  flagged « Changed since issued » and can be brought back to review at today's amount,
  zero included (a corrected slip).
- **Portal**: the payer reads and prints their own issued statements (a PDF in their
  language); a co-payer never sees them.
- **Export** of the issued statements of a year (CSV: payer, address, child, birth date,
  amount) for the software that produces the official slips. The file belongs to the
  person who exported it, in their companies, and is deleted with its wizard; a cell
  starting like a spreadsheet formula is neutralised. The family's PDF does not print the
  birth date.

## Limits

- This module does **not** produce the official Relevé 24 nor transmit it to Revenu
  Québec, and it does not collect social insurance numbers. The statement says so to the
  family.
- Revenu Québec's guide of the Relevé 24 (version 2025-10) and its requirements for slips
  produced by software were not read (the site refused the connection); the counting
  rule for a payment made in one year for services of another follows the letter of
  2017 and must be confirmed against the guide.
- The child-care share of a payer's services applies to every month of service, in proportion.
