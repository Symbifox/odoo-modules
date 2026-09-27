# Co-ownership: Electronic signature (`bf_property_sign`)

The bridge between the co-ownership suite and `bf_sign`. It wires signature onto
three kinds of document, and they do not have the same standing. The module says
so rather than treating them alike.

An auto-installing bridge: it installs itself when the suite and `bf_sign` are
both present, and the suite works perfectly without it.

## What it does

| Model | Document sent for signature | Default signer | When the button shows |
|---|---|---|---|
| `bf.property.maintenance.log` | the on-site examination declaration | the logbook's author, if they have an email address | once the logbook is established |
| `bf.property.attestation` | the art. 1068.1 CCQ attestation | nobody | always |
| `bf.property.assembly` | the general meeting minutes | nobody | once minutes exist |
| `bf.property.council.meeting` | the board of directors meeting minutes | nobody | once minutes exist |

Each model gains `bf.sign.mixin` and sends the report the suite already prints.
When the signature completes, the logbook records the declaration; the other
documents only get a line in their thread, and their state does not move.

## The one document a text actually requires to be signed

Regulation respecting various co-ownership rules, CQLR, c. CCQ, r. 8.01,
article 6: the person who establishes or revises the maintenance logbook
**signs** a declaration attesting that the common portions and the property
referred to in article 2 were examined on site by them or under their
supervision, and that they have taken cognisance of the information in the
logbook. The declaration is dated and included in the logbook.

A checkbox alone says something else entirely: it says a manager asserts that
the professional declared. That is exactly the distance article 6 closes by
naming the person who signs. Electronic signature closes it here too: the
logbook's author has no account in the instance and needs none. They get a link,
they sign, and their signature is what ticks the box and sets the date.

The button only appears once the logbook is established: a declaration signed
before the logbook exists would attest to having read information that is not
there yet.

The checkbox stays editable by hand, deliberately. A paper declaration signed and
filed in the logbook is just as valid, and a syndicate holding one does not have
to come through here to be in order. The bridge opens a path; it closes none.

The date written is the date of the signature, never today's. A declaration dated
the day the software learns of it would say the site visit happened that day. And
an existing date is never overwritten: a syndicate that had a paper declaration
and signs on top of it does not see its original date move.

## Signing is not issuing

Art. 1068.1 CCQ runs fifteen days from the co-owner's request, and it is the
**delivery** that stops the clock, not the signature. The two gestures stay
distinct: signing a document you have not yet delivered is normal, and believing
the signature is enough would miss a deadline the module otherwise tracks with
care. The thread line written at signature says so.

## Signing is not transmitting

Art. 1102.1 CCQ (general meeting) and art. 1086.1 CCQ (board meeting) both
require **transmission** to the co-owners within 30 days. Neither mentions
signature. That a chair and a secretary sign the minutes is a widespread
practice, often provided for in the declaration of co-ownership; it is not a
condition of validity drawn from the Code. The bridge offers it without ever
presenting it as an obligation, and nothing depends on it: signed minutes are
not transmitted for that, and the 30-day clock keeps counting on transmission
alone.

## The module never guesses who signs for the syndicate

The suite refuses to model the composition of the board of directors: the
declaration of co-ownership fixes it and it varies from one immovable to the
next. So the module cannot know who, at this syndicate, has capacity to attest.
It asks at sending time, rather than proposing someone who would look like the
right person because software picked them.

The logbook is the single exception, and for a reason: article 1 of the
regulation bounds who may establish a logbook, and the module already holds the
author with their professional order. An author without an email address is not
proposed, rather than invented.

## Security

The buttons are restricted to `bf_sign`'s signature **user** group, and the
co-ownership manager group implies that group. The manager can therefore send
minutes, an attestation or a logbook declaration for signature without being an
administrator, while the configuration of `bf_sign` stays with the
administrator.

## Dependencies

- `bf_property_records`
- `bf_property_governance`
- `bf_sign`

## Tests

9 tests: the logbook proposing its author and nobody else, an author without an
email not being invented, the syndicate's signer being asked and never guessed,
the signature carrying the article 6 declaration, a paper declaration keeping
its own date, signing the attestation not issuing it, signing the minutes not
transmitting them, every wired document pointing at a report that exists, and
the manager holding the signature tool as a user.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations, which include administering immovables that you own or
  that you are constituted to administer, and letting a person acting on your
  behalf use your instance for that purpose. A syndicate, a housing cooperative
  or a non-profit housing organisation running the module for its own immovable
  is covered, and so is the bookkeeper or the manager it hires who works inside
  its instance.
- **Requires a written agreement**: administering immovables for the account of
  others, and providing the module as a product or service to third parties,
  whether hosted, managed or resold.
- **Change Date**: on 2030-08-24, this version converts automatically to
  **LGPL-3.0-or-later**.

## Acknowledgements

Created and maintained by Les services de consultation Blue Fox, Inc. AI coding
assistants were used as productivity tools during development.
