# Subscriptions — Expense Reports (`bf_subscription_expense`)

A subscription paid with an employee's card counts in the subscription's costs.

## The problem

Some subscriptions are paid with a personal card and claimed back through an
expense report. They never produce a vendor bill. The subscription register
(`bf_subscription`) only read vendor bills, so such a subscription showed no
cost at all and was flagged as dormant, since no bill ever arrived.

## What it adds

- A **Subscription** field on the expense (form and search).
- An approved or paid expense linked to a subscription counts in its **total
  paid** and in its **last bill date**, so it is no longer reported dormant.
- An **Expenses** button on the subscription opens its expenses.

## One rule

**Link the expense, never the expense report's journal entry.** The report
produces one vendor bill in the employee's name that groups every expense of
the period. Linking that bill would add the other subscriptions' expenses too,
and count this one twice. The module refuses that link and never suggests it.

The subscription must belong to the expense's company: the link is refused
otherwise, including through the API.

The totals are read with full rights: someone who manages subscriptions but
cannot see other people's expenses still reads the right amount.

Draft, submitted and refused expenses do not count: nothing was decided yet, or
it was refused.

## Requirements

`bf_subscription` 18.0.1.8.0 or later, and `hr_expense`. The module does not
auto-install: install it explicitly where both are present.

## Languages

Labels and messages are written in English in the source; the French ships in
`i18n/fr_CA.po`.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-10-25, this version converts automatically to
  **LGPL-3.0-or-later**.

## Changelog

### 18.0.1.0.1

- The subscription must belong to the expense's company; a link to another
  company's subscription is refused.

### 18.0.1.0.0

- First release: a Subscription field on the expense, counted in the
  subscription's total paid and last bill date.
