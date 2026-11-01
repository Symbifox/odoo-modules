# Buildings: Access and Privacy (`bf_property_access_privacy`)

Bridge between resident access (`bf_property_access`) and the Québec privacy
register (`privacy_consent`), installed automatically when both are present.

## What it adds

- **A processing purpose, "Building access control"**, in the register of
  processing activities, with its information notice in French and English
  (s. 8 of the private-sector Act) and its retention policy (90 days after the
  move-out, deletion). It requires no consent: controlling who enters a
  building serves the lease and the occupants' safety (ss. 4, 5 and 9); the
  person is informed.
- **What is declared against what is done.** The Access tab of the organisation
  compares the period declared in the register with the period the
  organisation applies to its access journal, and says so when they differ or
  when nothing is purged at all. It never corrects either: which one is right is
  the organisation's decision.
- **A privacy impact assessment template** (s. 3.3), printable from the
  organisation: prefilled with the access points, credentials, connectors,
  resident counts by origin and retention, and the facts the software
  guarantees (no biometrics, no codes, no removal on a date, locks independent
  of the software), with boxes for the person in charge of protecting personal
  information to complete. It names no one.

## Security

The template is reserved to the property team (Consultation) on an
organisation they can read: reading the organisation is not enough, since
portal residents read their own. Its values are read with elevated rights after
that check, and only for that organisation. The purpose, notice and retention
policy belong to no company, so they apply to every organisation.

## Dependencies

`bf_property_access`, `privacy_consent`.

## Tests

11 tests: purpose, notice and policy declared, common to every company;
declared against applied retention (equal, different, never purged, archived
policy, company-specific policy, zero and zero); the template prefilled and
naming no one; printing requires the property team, not just read access to
the organisation (the portal reads it). Two adversarial reviews.

## Licence

Business Source License 1.1, see [`LICENSE`](LICENSE). Change Date 2030-11-01,
then LGPL-3.0-or-later. Same grant as `bf_property_access`.

## Acknowledgements

Created and maintained by Les services de consultation Blue Fox, Inc. AI coding
assistants were used as productivity tools during development.
