# Operating Budgets — Purchase Orders

Satellite of [Operating Budgets](../bf_budget): confirmed purchase orders enter
the committed amount of their budget item before any bill exists.

## What it adds

A confirmed purchase order is a decided expense: the supplier has the order,
the money will go out. The budget core only counts posted journal entries, so
an order placed in January would stay invisible until its bill is posted in
March — precisely the window where budget control is supposed to bite.

This module adds, to each budget line, the share of every confirmed purchase
order line that no posted bill carries yet. As soon as a bill is posted, that
share moves from "open purchase orders" into the actual amount: the committed
total does not move, it changes column.

## The guard is the posted entry, never Odoo's "billed quantity"

Odoo's `qty_invoiced` counts draft bills as well as posted ones. Relying on it
would make the commitment vanish the moment a draft bill is keyed in, before
the actual amount picks it up: the money would leave the budget for as long as
the bill sits unposted. This module instead subtracts the balance of **posted**
journal items linked to the order line — credit notes included, so a posted
refund returns its share to the committed amount.

The remainder never goes below zero: a bill beyond the order belongs to the
actual amount, where the overrun is visible, not hidden behind a negative
commitment.

## The theoretical amount follows the planned dates

A purchase order line carries a planned date: it is a dated commitment, like a
subscription renewal. When a budget item has some, the theoretical amount
becomes "what was due by now" instead of a fraction of elapsed time, and it
combines with the other commitment calendars of the item (such as the
subscription schedule) without ever counting the uncovered share of the plan
twice. The line states which basis it used.

## Known overlap: a renewal ordered through a purchase order

Nothing in Odoo links a purchase order to the subscription it renews. If a
dated renewal is also covered by a confirmed purchase order on the same budget
item, the committed amount counts both — the subscription's upcoming renewal
and the order's unbilled share — until the bill is posted. If your purchasing
flow goes through purchase orders, keep the subscription register out of the
budget item (or the other way around): one calendar per expense, never two.

## Planned dates and time zones

The planned date is a timestamp; the module reads it as the date the user's
screen shows. A line planned near midnight UTC can fall on a different day for
readers in different time zones — including the scheduled alert pass, which
reads it in the alert user's time zone. Day-level noise at the window's edges,
not a drift.

## Where an order lands

An order line is matched to its budget item through the expense account of its
product, fiscal position included — the same path its bill will take. The
planned date places it in the fiscal year. Sections, notes and down payments
carry no commitment of their own.

## Access

A budget user does not need purchase rights to see what is committed on their
position: amounts are read with elevated rights. The budget line only exposes
totals and counts, never an order or a supplier. Opening the orders themselves
goes through normal access rights.

## Licence

Business Source License 1.1 (see `LICENSE`). Each version converts to LGPL-3 on
its Change Date: 2030-11-04 for this one. In the meantime, use in production for
your own internal business operations is permitted.
