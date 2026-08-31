# Rolling Forecast — Seeding from Dated Commitments (`bf_budget_forecast_subscription`)

Replaces the flat average that seeds open forecast months with the actual
renewal calendar.

## The problem

The forecast base seeds open months with the average of the closed ones. It is
honest, but it is flat: a yearly renewal ends up spread over twelve months, and
the month it actually falls in is understated by its whole amount.

## What it does

It splits the seed in two.

**What is dated** lands on the month it falls in, read from the subscription
schedule of the position.

**The rest** is averaged — but only the rest. The average is computed on the
actual of closed months **minus what dated commitments already explained in
those months**.

That subtraction is the whole point. Without it the recurring part would be
counted twice, once on its date and once inside the average, and the
best-known part of the budget would become the worst-estimated one.

## On-demand subscriptions

They have no schedule, so they fall into "the rest", where the average catches
them. That is the desirable behaviour: better spread than lost.

## Licence

LGPL-3.
