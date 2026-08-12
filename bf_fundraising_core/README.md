# Fundraising — Core (`bf_fundraising_core`)

A donor management and fundraising platform for charities and non-profits,
built on top of the **Donations** module (OCA `donation`). Comparable to
**Raiser's Edge / Blackbaud**, in French, inside Odoo.

## What it adds

- **A 4-level fundraising structure**: Fund → Campaign → Appeal → Package,
  with computed goals, raised amounts and progress bars.
- **The Fund drives analytic accounting**: each fund points at an analytic
  account, and donation lines are allocated automatically.
- **An enriched constituent record** on `res.partner`: type (individual,
  household, organisation, foundation), household grouping, solicitation codes
  (do not solicit / do not call / etc.), giving summary (total, first gift,
  last gift, largest gift), capacity and wealth rating, major-gift prospect.
- **A lapsed donor report (LYBUNT)** and other segmentation filters, directly
  on the constituent list.

## Dependencies

`donation` (OCA), `analytic`, `bf_onboarding_base`.

The **compliant Canadian official receipt (CRA + Revenu Québec, in French)**
is provided by the companion module **`bf_receipt_ca`**. The web donation form
and the donor portal live in **`bf_fundraising_web`**.

## Licence

AGPL-3 (the module extends the OCA `donation` module, which is AGPL-3).
