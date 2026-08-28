# Symbifox dashboard (`bf_dashboard`)

A unified dashboard aggregating billing, hosting, knowledge matrices and
privacy into a single view.

## Features

- Summary cards aggregating several operational domains (billing, hosting,
  knowledge, privacy consents).
- Extensible: other modules can add their own cards (see
  `bf_subscription_dashboard`).
- Its own root menu, **Tableau de bord**, at sequence 2 — right after the
  `bf_home` landing screen.

## Dependencies

`base`, `account`, `project`, `mail`.

The modules the tiles draw from — `hosting_management`,
`project_knowledge_matrix`, `privacy_consent` — are **not dependencies**. They
are probed at runtime, on the `bf_home` pattern: every collector declares what
it reads with `@needs(model, field, ...)`, and its tile only appears when the
tenant carries that model with those fields. The module therefore installs on a
tenant that has all three exactly as it does on one that has none.

`account`, `project` and `mail` stay hard: the four accounting collectors query
`account_move_line` in raw SQL, out of the ORM's reach and so out of any
guard's.

## Missing, broken, empty

Three distinct states, which do not look alike:

| State | Payload | Screen |
|---|---|---|
| The module is not on this tenant | `None` | no tile at all |
| The collector raised | `None`, plus its key in `data["failed"]` | the tile says data unavailable |
| The module is there with nothing to show | dict of zeroes | the tile shows 0 |

`_safe()` opens a **savepoint** around every collector. That is what makes the
guard real: a collector exception almost always comes from inside a query,
which leaves the transaction aborted — without it the next collector fails in
turn and the page goes down anyway.

## The template is an interface

Four modules extend `bf_dashboard.Dashboard` by xpath, and two of those anchors
are literal expressions of the template:

| Module | Anchor |
|---|---|
| `bf_cx_dashboard` | `//t[@t-if='state.data.overdue_activities']` |
| `bf_subscription_dashboard` | `//div[@class='col-lg-4 mb-3'][.//*[@t-on-click='openPrivacyPending']]` |

In Odoo 18 those extensions are applied in the browser, so an extension xpath
that no longer resolves **does not raise**: the inheriting module's card simply
stops appearing. That is why a collector failure is reported through
`data["failed"]` rather than by a marker slipped into each section, which would
have forced those conditions to be rewritten.
`test_inheritance_anchors_still_resolve` freezes both anchors.

## Why a tile is silent

```python
env["bf.dashboard"]._diagnose()
```

Returns, for every guarded collector, `module absent`, `champ absent : ...` or
`actif`. Without it, a missing module and a misspelled field name are
indistinguishable: the tile disappears in both cases and nothing anywhere says
which. `tests/test_dashboard.py` asks the same question at install time.

## One door, one dashboard

`bf_home` is the landing screen: it sets an `ir.default` on
`res.users.action_id` and holds the **Accueil** menu at sequence 1. This module
is not a competing door, it is the dashboard `bf_home` does not provide —
twelve-month revenue, vendor bills to pay, reconciliation, drafts to validate,
plus the cards other modules graft onto it.

Up to 18.0.1.2.0 it did claim the door, and badly: a `post_init_hook` wrote
`action_id` on **every** internal user at each install, overwriting a personal
setting. The hook is gone, along with the `data/dashboard_data.xml` that
targeted `base.default_user`, and the module now carries its own `menuitem` at
sequence 2 — which it had been missing from the start: without one the screen
only opened through `/odoo/action-<id>` typed by hand, while four modules were
adding cards to it.

Upgrading from any earlier version runs `migrations/18.0.1.2.0`, which hands
back `base.default_user.action_id` — and only if it still points at this
module's action. Existing users' `action_id` is deliberately left alone: that is
their setting now, and taking it from them would be the very move being fixed.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-08-12, this version converts automatically to
  **LGPL-3.0-or-later**.
