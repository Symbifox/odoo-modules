# Co-ownership — Foundation (`bf_property_core`)

Structural foundation for managing Quebec *syndicats de copropriété divise*
(condominium associations) in Odoo 18 Community.

## What it does

| Model | Purpose |
|---|---|
| `bf.property.syndicat` | The legal entity, its declaration of co-ownership, and the quote-part base |
| `bf.property.building` | A building under the syndicat, with address and cadastral lot |
| `bf.property.unit` | A *fraction* (private portion) with its quote-part, type and area |
| `bf.property.ownership` | Ownership history per fraction, including *indivision* |
| `bf.property.common.area` | Common portions, general or restricted-use (art. 1043 C.c.Q.) |

## Design notes

**Quote-parts are checked, not enforced.** The total of all live fractions is
computed continuously and compared against the syndicat's declared base (1000
for millièmes, 10000 for dix-millièmes). A gap raises a visible banner and a
searchable state, but never blocks a save. A building is keyed in fraction by
fraction, and a hard constraint would make the first save impossible.

**Ownership is a history, not a pointer.** Article 1070 C.c.Q. requires the
syndicat to keep a register of co-owners and tenants. Knowing who owns a
fraction *today* is not enough, so ownership is a dated relation with an
overlap-aware constraint: simultaneous shares cannot exceed 100 %, while
consecutive owners at 100 % each are perfectly normal.

**Date-derived fields are refreshed nightly.** `is_current` and a fraction's
current owners are stored so they stay searchable, but they depend on today's
date rather than on a write. A daily cron re-flags the records whose window has
lapsed. Without it, an ownership ending 31 December would keep showing its
former holder as a current co-owner until someone happened to edit the record.

**Archiving is honest.** Archiving a fraction removes it from the quote-part
total, which will usually flip the syndicat to *incomplet*. That is intended:
if the live fractions no longer cover the declared base, the data no longer
matches the declaration and the banner should say so.

## Legal basis

Every rule encoded in this suite carries its primary source and its date of
coming into force: the consolidated *Code civil du Québec* published by the
Éditeur officiel, the regulations made under it, and the transitional sections
of the 2019 statute commonly called Loi 16. None comes from secondary
commentary. Where the text is silent, the module says so on screen rather than
inventing a threshold.

This is software, not legal advice. A syndicat remains responsible for the
decisions it takes.

## Scope

This module is structure only. Governance (assemblées générales, weighted
votes, minutes), common expenses, the *fonds de prévoyance* and the Loi 16
obligations (*carnet d'entretien*, *étude du fonds de prévoyance*,
*attestation du syndicat*) belong to separate modules that depend on this one.

## Dependencies

`base`, `mail`. Nothing else. No hard dependency on any other module of this
repository.

## Tested

20 tests on staging: structure and quote-part arithmetic, indivision, ownership
history windows, the guard rails, the refresh cron, and archiving behaviour.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations, which include administering immovables that you own or
  that you are constituted to administer, and letting a person acting on your
  behalf use your instance for that purpose. A syndicat, a housing cooperative
  or a non-profit housing organisation running the module for its own immovable
  is covered, and so is the bookkeeper or the manager it hires who works inside
  its instance.
- **Requires a written agreement**: administering immovables for the account of
  others, and providing the module as a product or service to third parties,
  whether hosted, managed or resold.
- **Change Date**: on 2030-08-22, this version converts automatically to
  **LGPL-3.0-or-later**.

## Acknowledgements

Created and maintained by Les services de consultation Blue Fox, Inc. AI coding
assistants were used as productivity tools during development.
