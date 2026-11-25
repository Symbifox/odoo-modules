# Symbifox Business Trips (`bf_expense_trip`)

A business trip gathers its receipts, its meal allowances, its mileage and its
hotel folio into one expense report. An administrative or executive assistant
can prepare it for the traveller.

> **Not tax advice.** Whether an allowance is reasonable, and so not taxable,
> is the employer's policy and depends on the facts. The module applies the
> company's rates; it does not decide them.

## Why

Odoo knows one expense at a time. A trip is a dozen of them, paid over several
days, and very often put together by someone other than the traveller: the
assistant who books, collects the receipts afterwards, splits the hotel bill,
counts the meals and builds the report.

## Features

- **Trip** (*Expenses › My Expenses › Trips*): traveller, dates, destination,
  purpose, customer, analytic distribution.
  - *Collect receipts*: the traveller's expenses dated within the trip, not in
    a report yet, are attached. A new expense is attached on its own when a
    single trip of the traveller covers its date.
  - *Create report*: one report for the trip, named after it. It makes two
    when the company paid some of it, as Odoo does. Expenses without an
    analytic distribution take the trip's.
  - *Add mileage*: the kilometres split between the two rates of the year
    (`bf_expense_mileage`), attached to the trip.
- **Allowances**: one line per day (breakfast, lunch, dinner, incidentals,
  private lodging). Untick a meal that was provided. *Generate allowances*
  makes one expense per allowance and rate, without taxes, and replaces the
  previous ones unless they are already in a report. *Create report* generates
  them again first, so the report holds what the days say now. New dates remove
  the allowances (they are generated again); once they are in a report, the
  dates are frozen.
- **Allowance rates** (*Expenses › Configuration › Allowance rates*): the
  company's own, each with a start date, so a rate that changes during a trip
  is applied day by day. *Load the federal rates* adds those of the National
  Joint Council, Appendix C, Canada, October 1, 2026 (breakfast 29.95,
  lunch 30.55, dinner 62.70, incidentals 25.00, private lodging 50.00) in a
  company that keeps its books in Canadian dollars.
- **Travel assistants** (employee form, next to the expense approver): the
  users who prepare this employee's trips and expenses. They choose the
  employee on an expense, create, attach, split and submit; once submitted,
  they only read. They **cannot approve** this employee's reports, even when
  they approve expenses otherwise: not by the button, not by creating a report
  already approved, not by naming themselves its manager. They cannot pull an
  approved, posted or paid report back to draft either (a submitted or refused
  one, yes). Administrators of Expenses keep the right, as Odoo gives it to
  them for their own reports.
- **Split hotel folio** (button on a draft expense): one line per charge, as
  printed, taxes included (room, lodging tax, meals, parking). Personal lines
  (minibar, a movie) are not reimbursed. Their total is kept on the expense
  and listed in its chatter. The lines must add up to the folio. The receipt is
  copied on each expense, and an expense in a draft report stays in it. A folio
  the company paid keeps every charge (the personal ones are owed by the
  employee, not removed), and an allowance is not a folio.

## Limits

- No booking, no advance, no budget compared with the actual: next lots.
- Allowances do not follow departure and return hours (Quebec's directive and
  the federal one reduce a partial day). Untick the boxes of a partial day.
- The 50 % restriction of input tax credits on meals and entertainment is the
  company's tax file, not this module.

## Licence

Business Source License 1.1, see `LICENSE`.
