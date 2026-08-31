# Operating Budgets — Recurring Commitments (`bf_budget_subscription`)

Turns the subscription register into a calendar of dated commitments, and lets a
budget stop waiting for the invoice.

## The problem

A budget that only counts posted invoices tells you what happened. But the most
predictable part of an operating budget is not a mystery at all: the renewals
are contractual, dated, and already known. Ignoring them until the bill arrives
means the budget is systematically blind to the one thing it could see coming.

## What it adds

- **A budget position on each subscription**, deduced from the dominant expense
  account of that subscription's own posted vendor bills. No hard-coded mapping
  between a vendor name and a position: that would work here and nowhere else.
- **Committed stops waiting.** Renewals falling inside the budget period are
  added to the committed amount. They are known, dated and contractual.
- **Theoretical follows the calendar, not the clock.** Instead of a fraction of
  elapsed time, the theoretical becomes "what was due by today", built from the
  renewal schedule.

## Three rules that protect the numbers

**Only what is still to come is added.** Anything already due is in the actual
the moment its bill is posted. Adding it again would double the most predictable
expense in the budget — the one everyone believes is best controlled.

**The part of the plan no dated commitment covers stays on a prorata.** A
position where subscriptions are half the budget must not lose the other half
from its theoretical.

**Ambiguity is reported, never guessed.** When two positions cover the dominant
account, or none does, the assistant says so and leaves the field empty. A
mapping guessed at random would skew a budget with nothing to trace it back to.

## An on-demand subscription has no calendar

It still spends. The module does not let it pass for a zero commitment in
silence: the budget line flags it, because a partial calendar mistaken for a
complete one understates the theoretical and manufactures false alerts.

## Licence

LGPL-3.
