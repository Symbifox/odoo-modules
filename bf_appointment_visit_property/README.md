# Property Showings — Immeubles bridge (`bf_appointment_visit_property`)

Links a showing listing to a `bf.property.unit` from the *Immeubles* socle.

## Why a separate module

A real estate broker has no property portfolio: their listings are mandates,
not assets. Making `bf_appointment_visit` depend on `bf_property_core` would
hand every broker a condominium registry they will never open. So the showing
module stands alone, and this bridge exists for the other half of the market:
whoever manages buildings and shows dwellings to prospective tenants.

## What it does

- Adds `unit_id` on the listing. Picking a unit fills in the address from its
  building, and sets the occupancy from the unit itself.
- A rented unit with a known occupant sets the listing to "occupied by a
  tenant", names the tenant, and raises the lead time to 24 hours right away.
  Doing it at that moment matters: otherwise the first window typed in the same
  breath gets refused for reasons nobody can see yet.
- Adds a **Faire visiter** button on the unit form, which opens the existing
  listing when there is one rather than creating a second.

Nothing moves out of the listing: it keeps its own address and occupancy
fields. Changing your mind about a showing must never mean editing the dwelling
record.

## Requirements

- `bf_appointment_visit` (property showings) and `bf_property_core` (the
  *Immeubles* socle), both published in this repository.
- Odoo 18 Community.

## License

This bridge is LGPL-3. ⚠️ Both modules it depends on are published under the
Business Source License 1.1, and this one does not load without them: read
their `LICENSE` before concluding that a deployment is LGPL-3 as a whole.
