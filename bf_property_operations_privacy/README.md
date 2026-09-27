# Operations: Privacy bridge, Act 25 (`bf_property_operations_privacy`)

A work shift names people and records what they did. That is personal
information, and Québec's Act 25 makes no exception for it because those people
happen to be employees. This bridge declares the shift in the register of
processing activities kept by `bf_property_privacy`.

## What it does

It adds one purpose to the register, **Maintenance team work shifts**
(`property_shift`), with a plain-language summary stating what is kept: the
time span, the team, the people assigned, and the maintenance work carried by
each shift (what was settled, what was passed to the next shift, what was
removed from the list, with the reason). The purpose requires no consent and no
express opt-in, and applies to all contexts and channels.

It contains no model, no view and no code: one data record.

## Its basis is neither consent nor an obligation of the Civil Code

The three purposes that `bf_property_privacy` declares rest on three different
things: art. 1070 para. 1 CCQ (the syndicate's register), an express consent
(text-message notices), and the duty to inform third parties who consented to
nothing (the parcel and visitor log). This one rests on the **employment
relationship**: the employer knows who held the shift because the employer
assigned it.

**And that is precisely why no consent is asked.** Asking a caretaker to
consent to it being known who did the rounds would suggest the request could be
refused, when refusing would mean refusing to account for one's work. The same
reasoning as for the syndicate's register, applied to a different regime: a
consent that cannot be withdrawn is not a consent, it is a form. The summary
says so to the person.

## What Act 25 does leave here

The duty to inform the person of the processing and of its purpose, and the duty
not to keep beyond necessity. The module declares the purpose. It purges
nothing, and it does not pretend to.

## The shift is not a disciplinary file

What gets recorded is the work: which round, which work order, settled or
handed to the next shift. No appraisal, no performance rating, no attendance,
no hours worked. Odoo has `hr_attendance` for hours, and the shift stops at the
work. The plain-language summary says so, and a test asserts that it keeps
saying so.

## The shown text is not rewritten

The plain-language summary is what was *shown to people*. The record is loaded
with `noupdate="1"`, so a module upgrade cannot change after the fact what they
read. A test asserts the flag, because the flag is the whole guarantee.

## It installs itself

An auto-installing bridge, on the `bf_property_privacy` pattern. Declaring the
shift's purpose inside `bf_property_privacy` would have written it into the
register of every database, including those where no shift exists, and a
register announcing a processing activity that does not take place is as false
as one that hides one.

## Dependencies

`bf_property_operations`, `bf_property_privacy`. Auto-install.

## Tests

8 tests: the purpose exists and is distinct from the suite's three, no consent
or opt-in is required and no consent notice hangs off it, the summary states
what is collected and denies what the shift is not, the record is `noupdate`,
and the bridge installs itself when both sides are present.

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
- **Change Date**: on 2030-08-30, this version converts automatically to
  **LGPL-3.0-or-later**.

## Acknowledgements

Created and maintained by Les services de consultation Blue Fox, Inc. AI coding
assistants were used as productivity tools during development.
