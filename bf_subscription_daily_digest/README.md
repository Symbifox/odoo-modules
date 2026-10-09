# Subscriptions — daily digest section (`bf_subscription_daily_digest`)

A bridge module that injects an **"Upcoming renewals"** section into the daily
digest email (`daily_todo_digest`).

Auto-installs when `bf_subscription` **and** `daily_todo_digest` are both
present.

This daily section is a simple heads-up, distinct from the full subscription
recap (`subscription.digest`) shipped inside `bf_subscription`: that one stays
the official channel for the detailed report (spend summary, dormant
subscriptions, cost per managed client, periodic send), while this bridge just
slips a reminder of imminent renewals into the daily digest email you already
receive.

## Dependencies

`bf_subscription`, `daily_todo_digest`.

## Languages (v18.0.1.1.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English. The section follows the language of each digest recipient, like the digest itself.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-08-12, this version converts automatically to
  **LGPL-3.0-or-later**.
