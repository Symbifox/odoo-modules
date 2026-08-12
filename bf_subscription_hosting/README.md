# Hosting — bridge to subscriptions (`bf_subscription_hosting`)

A bridge module between [`hosting_management`](../hosting_management) and
[`bf_subscription`](../bf_subscription).

## Why

Recurring infrastructure costs (domain names, SSL renewals, services) are often
entered **twice**: once as a hosting domain (`hosting.domain`) and once as a
subscription (`subscription.subscription`). This bridge removes that double
entry.

## What the module does

Adds a **"Create a subscription"** button to the header of the hosting domain
record. It creates a **draft** subscription prefilled from the domain's data:

| Domain (`hosting.domain`) | Subscription (`subscription.subscription`) |
| --- | --- |
| `name` | `name` ("Domain name — …") |
| — | `category` = `domain_name` |
| `annual_cost` | `cycle_amount`, `cycle` = `annual` |
| `currency_id` | `currency_id` |
| `partner_id` | `vendor_id` (review it — see below) |
| `date_expiration` | `start_date` anchor (→ next renewal) |
| `auto_renew` | `auto_renew` |
| `registrar` | `external_reference` |

A link field is stored in **both directions**
(`hosting.domain.subscription_id` ↔ `subscription.subscription.hosting_domain_id`)
so the button hides once the subscription exists: no duplicates. A smart button
opens the linked subscription.

**No automatic background synchronisation**: creation is deliberate, one-off,
and the subscription stays a **draft** for review before activation.

> The subscription's `vendor_id` field (the vendor/registrar) is required.
> Since `hosting.domain` does not store the registrar as a partner, it is
> prefilled with the domain's `partner_id` (or the company); adjust it as needed
> before activating the subscription.

## Installation

Auto-installs when `hosting_management` **and** `bf_subscription` are both
present (`auto_install: True`).

## Dependencies

`hosting_management`, `bf_subscription`.

## Licence

Distributed under the **LGPL-3** licence. See the [`LICENSE`](LICENSE) file.
