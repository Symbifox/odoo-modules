# Rental: Privacy bridge, Act 25 (`bf_rental_privacy`)

The rental side of the suite collects two things that the register of processing
activities must name, and they are not alike. This bridge writes both into the
register, each as its own purpose, with a plain-language summary of what is
collected and why.

The module holds data only: two purposes, no model and no view.

## Two purposes, never one

**The lease file: lease, notices and rent.** The lessee's name, address, contact
details, the agreed rent and what was paid, and the notices exchanged by either
side (modification of the lease, repossession, eviction, assignment, sublease).
This is ordinary, and it is the very keeping of the rental file. It rests on the
lease and on the obligations the Civil Code attaches to the lease of a dwelling,
not on consent. The summary also says what the file does *not* hold: no security
deposit, no credit check, no appraisal of the person. The law forbids requiring
a deposit, and the suite has no field to carry one.

🔴 **The ground of a resiliation by the lessee.** The Civil Code lets a lessee
resiliate before term in certain situations: allocation of a low-rental dwelling,
rehousing ordered by the court, a handicap preventing occupancy, permanent
admission to residential care (art. 1974 CCQ), or a threat to the lessee's safety
or that of a child living in the dwelling (art. 1974.1 CCQ). The last one reveals
that a person is a victim of sexual violence, conjugal violence or violence
against a child. Its disclosure does not cause an inconvenience: it puts a
person in danger.

Merging that ground into the ordinary lease file would mean announcing "name,
address and rent" to a person about whom the lessor actually records that they
are a victim of violence. A register must say what is collected, not an average
of what is collected. A test asserts that the two purposes are distinct records
with distinct codes.

## What the rental notices module already does, and the register documents

The summary of the resiliation ground tells the person what protects them, and
each point matches `bf_rental_notice`:

- The lessor keeps the ground invoked and whether the attestation was received,
  **not the content of the attestation** nor the facts behind it.
- Reading the ground is reserved to property managers, together with the two
  fields that would reveal it indirectly (the authority issuing the attestation,
  and the flag marking a sensitive ground).
- The ground is not tracked on the thread, so a change is not emailed to
  followers whose composition the person does not know.
- The date the lease ends stays visible: the reason is protected, not the date.

## What this module does not claim

Neither purpose asks for consent, for two different reasons. The lease file
rests on the lease itself. The resiliation ground rests on the Civil Code's
organisation of a right that belongs to the lessee: asking the lessee to consent
to the lessor knowing why they leave would suggest they could refuse and leave
anyway.

⚠️ The legal basis of the resiliation ground is recorded as a legal obligation:
the lessor receives the notice and the attestation because the Civil Code
organises the exercise of the lessee's right that way. **That qualification has
not been validated by legal counsel.** A register that hides a processing
activity is more false than one whose basis remains to be refined, which is why
the purpose is written in rather than held back. The plain-language summary says
what is collected and what is done with it, the most concrete obligation of
Act 25; the qualification of the basis will be corrected if a legal opinion
contradicts it.

## The shown text is not rewritten

The plain-language summaries are what was *shown to people*. Both records are
loaded with `noupdate="1"`, so a module upgrade cannot change after the fact what
they read. A test asserts the flag, because the flag is the whole guarantee.

## It installs itself

An auto-installing bridge, on the `bf_property_operations_privacy` pattern.
Declaring these purposes inside `bf_property_privacy` would have written them
into the register of a syndicate that lets nothing, and a register announcing a
processing activity that does not take place is as false as one that hides one.

## Security

No new group, access right or record rule. The restrictions on the resiliation
ground described above live in `bf_rental_notice`; this module only documents
them in the register.

## Dependencies

`bf_rental_notice`, `bf_property_privacy`.

## Tested

8 tests: both purposes are registered and distinct, neither asks for consent,
the lease summary denies the deposit and the credit check, the ground summary
says the attestation is not kept and explains the protection, the records are
`noupdate`, and the bridge installs itself.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations, which include administering immovables that you own or
  that you are constituted to administer, and letting a person acting on your
  behalf use your instance for that purpose. A landlord, a housing cooperative
  or a non-profit housing organisation running the module for its own immovable
  is covered, and so is the bookkeeper or the manager it hires who works inside
  its instance.
- **Requires a written agreement**: administering immovables for the account of
  others, and providing the module as a product or service to third parties,
  whether hosted, managed or resold.
- **Change Date**: on 2030-08-30, this version converts automatically to
  **LGPL-3.0-or-later**.

## Acknowledgements

Created and maintained by Les services de consultation Blue Fox, Inc. AI coding
assistants were used as productivity tools during development.
