# Co-ownership: Privacy bridge, Act 25 (`bf_property_privacy`)

A syndicate that keeps the register of art. 1070 CCQ processes personal
information, and Québec's Act 25 makes no exception for it. This bridge writes
into the register of processing activities what the suite already collects, with
its legal basis and its retention period.

An auto-installing bridge: it installs itself when both sides are present, and
the co-ownership suite works perfectly without it.

## What it does

- Declares three purposes in the register of processing activities, each with a
  plain-language summary: the syndicate's register, text-message notices to
  occupants, and the parcel and visitor log.
- Declares the consent notice for text messages (French and English) with its
  first version, and a retention policy of 90 days for the parcel and visitor
  log.
- Mirrors each text-message consent (`bf.property.sms.consent`) into a formal
  consent file (`privacy.consent`): when a consent line is recorded, a granted
  file opens with it, dated from the consent and attached to the notice; when
  the line is withdrawn, the file is withdrawn the same day. The consent form
  shows the file and its status.
- On the organisation form, compares the retention the register declares with
  the purge period the syndicate actually applies, and shows the gap.

## Three purposes, three regimes that must not be confused

**The syndicate's register.** The name and postal address of each co-owner and
each occupant are in the register *by operation of law* (art. 1070 para. 1 CCQ).
There is no consent to ask for, and asking for one would suggest it could be
refused. The purpose is declared with no consent requirement, and a test asserts
that.

**Text-message notices.** The telephone number is in the register only "if that
person expressly consents to it" (same paragraph, different regime). Express
opt-in, revocable, dated. The suite already keeps that consent as a record; the
bridge stops it from being an island.

🔴 **The parcel and visitor log.** This one carries information about *third
parties who consented to nothing*. The visitor is neither a co-owner nor an
occupant, and has no account from which to withdraw anything. What Act 25 leaves
here is the duty to inform and the duty not to keep beyond necessity.

## The declared retention has to be the one that applies

⚠️ A retention policy that announces something other than what the code does is
worse than no policy: it hands the syndicate a ready answer to a question an
investigator asks differently. Not "what did you write down", but "what did you
keep".

The occupant portal purges the parcel and visitor log according to a period
**each syndicate chooses**; the register of processing activities carries a
single policy. The two can drift, and nobody would notice. The bridge compares
them and says so on screen. It corrects neither: which of the two is right is
the syndicate's decision, not the software's.

⚠️ **Zero is not a short period, it is the absence of a purge.** A syndicate may
legitimately manage retention some other way, but a declared period while nothing
is being deleted is exactly the gap this field exists to show.

The 90 days declared are the portal's default, not a period imposed by law: no
provision sets how long a syndicate keeps its reception log.

## A mirror, not a valve

⚠️ What decides whether a text message goes out remains
`bf.property.sms.consent`, and it keeps deciding alone. Making the send depend on
the Act 25 file would introduce a second switch, and therefore a day when the two
disagree and the syndicate cannot tell which to believe. A test revokes the
formal file, leaves the property consent standing, and asserts the message still
goes out.

⚠️ **A compliance file that failed to open never blocks a consent.** It is a
problem to fix, not a reason to stop a syndicate from collecting the consent it
is required to collect. The failure is logged and the screen shows the line as
unlinked.

⚠️ **Nothing is created retroactively.** Installing this bridge on a database
that already holds consents does not manufacture Act 25 files dated today: that
would produce a proof chain lying about its own date. Earlier lines keep their
date, stay unlinked, and the form says why.

## What this bridge does not do

It destroys nothing of its own. The log purge already exists in the occupant
portal, it deletes rather than archives, and it is covered by tests. Routing the
same records into a second destruction mechanism would risk certifying a
destruction nobody carried out.

## Security

No new group, access right or record rule. The formal consent file is opened and
closed with elevated rights, so a property manager recording a consent does not
need rights on the privacy register itself. The purposes, notice and retention
policy are loaded with `noupdate="1"`, so a module upgrade does not rewrite what
people were shown.

## Dependencies

`bf_property_portal`, `bf_property_sms`, `privacy_consent`.

## Tested

14 tests: the file opens with the consent, carries the notice version, is dated
from the consent and closes with the withdrawal; the file does not decide whether
a text goes out; a failed file never blocks a consent; retention matching,
diverging and absent; the three purposes and their consent requirements; the
bridge destroys nothing of its own.

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
- **Change Date**: on 2030-08-23, this version converts automatically to
  **LGPL-3.0-or-later**.

## Acknowledgements

Created and maintained by Les services de consultation Blue Fox, Inc. AI coding
assistants were used as productivity tools during development.
