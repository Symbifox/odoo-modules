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

## A narrowed line carries nothing from the register (v18.0.1.4.0)

A subscription is attached to a budget position; the register knows nothing
about the analytic axis. Nothing says which share of a subscription belongs to
the analytic account a narrowed line watches — and handing the full amount to
every narrowed line of the position would count the same renewal several times,
while its actual arrives weighted by the distributed share. So a line narrowed
to analytic accounts carries nothing from the register: the plain line of the
same position carries it all. Ambiguity is reported, never guessed.

**And the line says so.** Carrying nothing turns the recurring-commitment block
invisible, which would leave the reader of a narrowed line unable to tell that
the budget item has subscriptions at all — a figure amputated in silence. So a
narrowed line whose budget item carries subscriptions states it: how many the
item carries, and that this line carries none of them. It is a signal, never a
guessed share. It is not the on-demand warning above, which says something else
(a subscription that spends without a schedule).

## Opening the register needs the right to read it (v18.0.1.4.0)

The subscription pool behind the amounts is read with elevated rights, so that
a budget user without subscription rights reads the same committed amount as
everybody else. The records themselves are another matter: the two fields that
carry subscriptions and the method that opens them are reserved for users who
can read the register (`bf_subscription`). The restriction sits on the fields,
not only on the screen — a field hidden from the form still answers an RPC
`read`. Sums, counts and the calendar stay readable by every budget user: the
figure is stable by role, the detail is not.

⚠️ **Nobody is refused today, and that is on purpose.** `base.group_user`
implies `bf_subscription.group_subscription_user`, so every internal user may
read the register: no existing role loses anything to this restriction, and no
screen was breaking. The guard is there for the day the register's access is
tightened — it names vendors and amounts — and so that this module's shape
matches its sibling `bf_budget_purchase`. Who may read the register stays
`bf_subscription`'s decision, not the budget's.

## One calendar among others (v18.0.1.3.0)

The renewal schedule is now declared to the base module (`bf_budget` 18.0.1.4.0
or later, required), which combines every dated calendar a position carries —
renewals here, confirmed purchase orders in `bf_budget_purchase` — before
prorating the uncovered share of the plan once. Two refinements show in the
numbers: the uncovered share follows the monthly spread entered on the line
instead of a linear fraction of the fiscal year, and a subscription whose
renewals all fall outside the period no longer switches the line's theoretical
basis to "calendar" on its own.

## Languages (v18.0.1.1.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English. In English, the budget position a subscription is attached to is labelled **Budget item**, so that it no longer reads like a budget line.

## Licence

Business Source License 1.1 (see `LICENSE`). Each version converts to LGPL-3 on
its Change Date: 2030-11-10 for this one. In the meantime, use in production for
your own internal business operations is permitted.
