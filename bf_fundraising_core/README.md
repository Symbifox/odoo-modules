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

## Languages (v18.0.1.1.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English. The Fundraising app menu, which belongs to the `donation` module and which this module renames, is now named in every installed language that has a translation (*Levée de fonds* in French); French users used to read the `donation` module's own *Don*. On upgrade, the solicitation codes and onboarding texts still carrying the delivered French switch to English; edited ones are kept.

## Licence

AGPL-3 (the module extends the OCA `donation` module, which is AGPL-3).
