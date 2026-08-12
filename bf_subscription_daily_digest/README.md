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

## Licence

Distributed under the **LGPL-3** licence. See the `LICENSE` file.
