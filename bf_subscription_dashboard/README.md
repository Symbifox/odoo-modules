# Subscriptions — dashboard card (`bf_subscription_dashboard`)

A bridge module that adds a subscription summary card (monthly-equivalent
spend, upcoming renewals, dormant subscriptions) to the Symbifox dashboard
(`bf_home`).

Auto-installs when `bf_subscription` **and** `bf_home` are both present.

The card shows only to holders of the subscription register (assigned since
`bf_subscription` 18.0.1.10.0, v18.0.1.1.1): for anyone else the column is not
drawn at all, and no access error is logged on each dashboard load.

## Dependencies

`bf_subscription`, `bf_home`.

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
