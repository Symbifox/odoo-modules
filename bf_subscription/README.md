# Subscriptions (`bf_subscription`)

A single register for paid subscriptions: SaaS, corporate memberships,
infrastructure, certificates, domain names, recurring professional services.

## Features

- Track subscriptions held **in your own name** or **managed on a client's
  behalf**.
- Manual and automatic correlation with vendor bills (`account.move`).
- Rebilling to the client in 3 modes: at cost / with a margin (%) / fixed
  amount.
- Automatic reminders ahead of renewal (Odoo activity).
- Dashboard view: monthly-equivalent cost, upcoming renewals, consolidated MRR.
- Smart buttons on the partner record (managed / billed subscriptions).

## Report and dashboard

- **Built-in recap**: this base module ships its own `subscription.digest`
  model (*Recap* menu), which generates a PDF subscription report (spend
  summary, upcoming renewals, dormant subscriptions, cost per managed client)
  and can send it periodically. A `subscription.digest` configuration ships by
  default, in "on demand" mode (`auto_send = False`).
- **Dashboard card (MRR)**: the Subscriptions summary card on the Symbifox
  dashboard is **not** provided by this module. It is added by the separate
  `bf_subscription_dashboard` bridge, which auto-installs when
  `bf_subscription` and `bf_dashboard` are both present.

## Dependencies

`base`, `mail`, `account`, `analytic`, `bf_onboarding_base`.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-08-12, this version converts automatically to
  **LGPL-3.0-or-later**.

## Changelog

### 18.0.1.3.1

- Removed client names from the help and onboarding texts (public-release
  artefact); added the `LICENSE` file.
