# Rental: Tenant Access (`bf_rental_access`)

Bridge between the dwelling lease (`bf_rental`) and resident access
(`bf_property_access`), installed automatically when both are present.

## What it adds

- **Signatories become residents.** Every tenant of an active lease is a
  resident of its unit (or of the building alone, for a room let without a
  registered unit) from the lease's start date. Before it, they are *upcoming*
  and receive nothing; a manager can bring the arrival forward.
- **What makes a departure to confirm**, without removing anything:
  - the person is no longer a signatory of the lease;
  - the lease is archived;
  - a more recent lease has started on the same unit (a renewal written as a
    new lease keeps the same tenant, who is then simply followed to it);
  - a departure without notice was recorded (`bf.rental.abandonment`);
  - "Rent due until" has passed (with `bf_rental_rent`);
  - a resiliation by the tenant has taken effect (with `bf_rental_notice`).
- **The lease end date is never read**: a fixed-term lease renews by operation
  of law (art. 1941 CCQ).
- **The lease prevails on its unit.** A current lease removes the register
  occupant and the co-owner as sources for that unit (the register often keeps
  a former tenant); an ended lease only removes its own signatories from the
  register, and leaves a co-owner who took the unit back.
- A lease whose unit lies in another building is ignored and logged. Parking
  and storage leases make no resident.
- Saving a lease or recording an abandonment refreshes the residents of the
  building at once. That refresh runs in a savepoint: a failure is logged and
  never prevents the lease from being saved; the nightly run catches up.

`bf_rental_rent` and `bf_rental_notice` are not dependencies: their signals are
read only when they are installed.

## Dependencies

`bf_property_access`, `bf_rental`.

## Tests

19 tests, including: a past end date is not a departure, rent due until,
abandonment, a co-tenant removed, a newer lease, a renewal as a new lease, a
room without a unit, a declared spouse who stays (art. 1938), a lease saved
while the access refresh fails.

## Licence

Business Source License 1.1, see [`LICENSE`](LICENSE). Change Date 2030-11-01,
then LGPL-3.0-or-later. Same grant as `bf_property_access`.

## Acknowledgements

Created and maintained by Les services de consultation Blue Fox, Inc. AI coding
assistants were used as productivity tools during development.
