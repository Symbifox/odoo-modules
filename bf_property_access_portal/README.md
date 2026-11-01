# Buildings: My Access on the Portal (`bf_property_access_portal`)

Bridge between resident access (`bf_property_access`) and the building portal
(`bf_property_portal`), installed automatically when both are present.

## What it adds

- A **My access** page (`/my/property/access`) and a card on the portal home:
  every access point the person can open, the credential (directory entry,
  card, PIN, mobile credential, key), its reference such as a card number,
  and its status, including what is still being prepared, what awaits the
  two consents of a unit door, and what is about to be removed.
- A note that sends lost cards and missing access to a maintenance request,
  and reminds the resident that no code is kept and that the lock of their
  dwelling does not change without their consent (art. 1934 CCQ).

## Visitors

When announcing a visitor, the resident can tick "Give them temporary access to
the common doors": the building's entrances and elevators only, never a unit
door, the garage or a parcel locker, for the visit's window (two hours by
default), capped by the organisation. Cancelling the visit cancels the access;
a visitor marked as gone ends it. The status of the access shows next to the
visit. Only a resident who lives in the building can give it; when no unit is
chosen, the building where the host lives is used (if there is only one).

## Security: who sees what

- Portal users read access rights through one record rule: their own
  partner, and only rights in force or pending. Removed and cancelled rights
  are history, not access, and do not show.
- The page searches under that rule, then reads the building, unit and access
  point names with elevated rights on the records the search returned only:
  the portal never needs to read access points or buildings to know which door
  it opens.
- The page searches with exactly the rule's domain (the person, rights in force
  or pending) with elevated rights, behind the access-rights check: a resident
  whose building belongs to another company than their account still sees
  their doors, as the portal already shows their units.
- When the privacy bridge is installed, the page shows the "Building access
  control" notice (s. 8 of the private-sector Act).
- No code is displayed: the module keeps none.

## Dependencies

`bf_property_access`, `bf_property_portal`.

## Tests

8 tests: own rights only (read and write refused on someone else's), removed
rights leave the portal, the page lists one's own doors and no one else's
(internal users included), the home counter counts rights in force or pending,
a resident whose building is in another company still sees their doors, the
privacy notice shown on the page, and the privacy impact assessment refused to
the portal. Two adversarial reviews.

## Licence

Business Source License 1.1, see [`LICENSE`](LICENSE). Change Date 2030-11-01,
then LGPL-3.0-or-later. Same grant as `bf_property_access`.

## Acknowledgements

Created and maintained by Les services de consultation Blue Fox, Inc. AI coding
assistants were used as productivity tools during development.
