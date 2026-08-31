# Operating Budgets — Rolling Forecast (`bf_budget_forecast`)

A budget is set once and measured against. A forecast is **re-made**, every
month, over a horizon that moves with the calendar.

## What it adds

- **A horizon that crosses the fiscal year.** Twelve to eighteen months ahead,
  not one exercise. Nothing here assumes twelve months or a January start.
- **An actuals cut-off.** Months ending on or before that date are actual, read
  from the books. Months beyond it are forecast.
- **Vintages.** Each monthly pass is its own record, numbered, and keeps its
  figures forever. Publishing freezes one. You do not correct a past forecast:
  you make a new one.
- **Roll forward.** One button: the horizon advances a month, the actuals line
  gains a month, and every figure that already existed is carried over
  unchanged.

## What is stored, and what is not

The budget module never stores an actual — it recomputes it. A forecast obeys
the same rule seen from the other side: it stores the **decision** (what you
forecast) and keeps computing the **fact** (what happened).

Without that, the only question a rolling forecast can answer would have no
answer: *what did we believe in March, and what actually came?* That does not
reconstitute itself from nothing.

## Copying is not forecasting

A closed month cannot be forecast again — its actual is known. But rolling
forward **copies** the historical forecast into months that have since closed,
and must be allowed to: that copy is the vintage's memory. Two gestures write
the same field and do not mean the same thing.

## Seeding, and why it decides whether the module is used

Open months are pre-filled from the average of the closed ones. It is coarse but
honest, and it supposes nothing. A satellite that knows a schedule of dated
commitments replaces it with something better.

This is not a convenience. **A forecast nobody re-makes is worse than no
forecast**: it keeps looking authoritative while it rots. Everything here is
tuned so the monthly pass takes minutes — automatic seeding, one roll-forward
button, and a handful of positions rather than thirty.

## No drivers, on purpose

No formulas, no quantity × rate. One forecast per position and per month,
seeded automatically, corrected by hand where you know better. Drivers can be
grafted onto `_seed_value_for` later without breaking anything.

## Licence

LGPL-3.
