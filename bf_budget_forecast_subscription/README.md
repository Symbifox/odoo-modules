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

## Who sees the subscriptions (v18.0.1.1.1)

Since `bf_subscription` 18.0.1.10.0 the subscription register is assigned rather than granted to every employee. The dated amount of a forecast line is computed with elevated rights and
stays the same for everybody; the list of subscriptions behind it is readable
only by register holders, including through the API.

## Languages (v18.0.1.1.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-11-26, this version converts automatically to
  **LGPL-3.0-or-later**.
