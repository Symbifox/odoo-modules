# Operating Budgets — Expense Reports (`bf_budget_expense`)

Approved expense reports count as committed before they are posted.

## The problem

An approved expense report is a decision: someone signed off, and the money will
go out. A budget that only reads posted entries does not see it until the
accountant posts it, which can be days or weeks later. In the meantime the
budget looks better than it is.

## What it adds

- **Approved, not yet posted** expenses are added to the committed amount of the
  budget position whose account they will hit.
- **Submitted, not yet approved** expenses are shown on the budget line for
  information only. Nobody has approved them: they are in no amount.
- A button opens the underlying expenses, for users allowed to approve them.

## Three rules that protect the numbers

**The guard is the journal entry, never the status.** In Odoo, an expense whose
entry is posted but not yet paid still reads "Approved". Filtering on that
status would count it twice: once in the actual through its posted entry, and
once more in the committed amount. The module looks at the entry itself: a draft
entry is committed, a posted entry is actual, never both. When the entry is
posted, the amount moves from one column to the other and the committed total
does not change.

**The draft entry is the source of truth.** Since Odoo 18, approving an expense
report creates its journal entry as a draft. The module reads that entry's
lines: account, date and amount are exactly what will be posted, including any
correction an accountant made in between. Only a report with no entry at all
(submitted, or approved with its entry deleted) is computed from the expense.

**Only the expense part counts.** Recoverable taxes go to their own accounts and
are not an expense of the position. A non-recoverable tax stays on the expense
account and is included. The computation goes through Odoo's own tax engine, the
same one posting uses, so the committed amount matches what will be booked.

## Credit notes and reversals

A credit note or a "reverse and create draft" on an expense bill is followed
through the journal item's link to the expense, which Odoo copies, rather than
the entry's link to the report, which Odoo erases. A plain duplicate of an
expense bill is ignored: its expense is already carried elsewhere.

One case stays committed on purpose. When an expense entry is fully reversed
and nothing replaces it, the expense is back to "approved, not booked" and
counts as committed again. For an employee-paid expense, Odoo itself still
offers to post the report. For a company-paid expense, Odoo shows the report as
paid: if the correction is booked by a manual entry without the expense link,
review the budget line, which will count it on both sides until the report is
refused or reset.

## Access

A budget user does not need expense or accounting rights to see what is
committed on their position: amounts are read with elevated rights. The budget
line only exposes totals and counts, never an expense, an employee or an amount
per person. Opening the expenses themselves goes through normal access rights.

## Requirements

`bf_budget` and Odoo's `hr_expense`. The module installs itself as soon as both
are present.

## Licence

Business Source License 1.1 (see `LICENSE`). Each version converts to LGPL-3 on
its Change Date: 2030-10-27 for this one. In the meantime, use in production for
your own internal business operations is permitted.
