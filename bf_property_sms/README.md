# Co-ownership: Text message notices (`bf_property_sms`)

The bridge between the occupant portal and text messaging. It does one thing:
tell somebody their parcel has arrived, or that an urgent notice concerns them.
It receives nothing and holds no conversation.

An auto-installing bridge: it installs itself when both `bf_property_portal` and
`bf_sms_archive` are present, and the suite works perfectly without it. An
organisation with no messaging line has nothing to uninstall.

## What it adds

| Where | What |
|---|---|
| `bf.property.sms.consent` (new) | One consent per person and organisation: the number to use, the channels accepted (parcels, urgent notices), the date given, the date withdrawn, and how the consent was collected. Tracked on its own thread. |
| `bf.property.organisation` | The channel switch (off by default), the sending line chosen among the `bf_sms_archive` lines, and the list of consents. |
| `bf.property.parcel` | On creation, a text to the recipient if they consented to parcel notices. |
| `bf.property.announcement` | A **Send by text** button (shown once published and not yet sent, with an irreversibility confirmation), the date sent and the number of people reached. |

## Why a consent record and not a checkbox

**A phone number is not in the register by right.** Art. 1070 para. 1 CCQ puts
the name and postal address of each co-owner, and of each occupant, in the
register, and adds that it may also contain other personal information
concerning a co-owner or another occupant **if that person expressly consents
to it**. A phone number is exactly one of those.

So the consent is a record, dated, and three things follow that a checkbox
cannot do:

- **Consent is given to an organisation, not to the world.** Someone who owns
  here and rents elsewhere may want to be reached on one side and not the
  other. A consent can only be recorded for a person who has a fraction in that
  organisation, and there is at most one per person and organisation.
- **It is dated, and it is withdrawn rather than erased.** A consent with no
  date is worth little, and erasing it on withdrawal would destroy the ability
  to say what was permitted at the time of a message already sent. Withdrawal
  stamps the date and leaves the line; a withdrawal cannot predate the consent.
- **The number lives with it.** On the partner it would be visible to everything
  that reads a partner; here it serves only what it was given for. No sending
  path looks for a number anywhere else (not `partner.mobile`, for instance).

## Two switches, not one

The organisation opens the channel and chooses the sending line. The person
consents to be reached, channel by channel. **Both are off by default, and the
first without the second sends nothing.** An open channel with no sending line
chosen sends nothing either, and says so in its own words: "the channel is
closed" and "no sending line is chosen" are two different reasons, pointing at
two different settings.

## Sent on behalf of the line's owner

`bf_sms_archive` only lets the owner of a line, its members and the SMS manager
send from it. A caretaker recording a parcel is usually none of those. By
choosing the line on the organisation, the organisation has given the right to
speak through it; the message is therefore sent as the **owner of the chosen
line**. The urgent send is guarded before that point: it requires the suite's
manager (below).

## What the message says, and what it does not

"Organisation X: a parcel is waiting for you at reception." The name of the
organisation, and nothing else. **No door number, no carrier, no tracking
reference**: none of that is needed for somebody to come down and collect a
parcel, and all of it would cross a telecommunications network.

An announcement sent as an urgent notice carries the organisation's name and
the announcement's title; the reader goes to the portal for the rest.

**The announcement's audience outranks the consent.** A notice addressed to
co-owners does not reach a tenant, even one who consented to urgent notices.
Consenting to be reached is not consenting to be shown what is not your
business.

**Nothing goes out on its own.** Sending an announcement is a button, not an
effect of publishing: a notice is often published well before it is urgent, and
"urgent" is a judgement the organisation makes, not the module. The
announcement must be published first, and it is sent only once. The
announcement's thread records how many people were reached and who was not,
with the reason.

**What the carrier keeps does not follow the purge.** The parcel and visitor
logs erase themselves after the retention the organisation set. The messaging
provider keeps what it keeps, and so does the recipient's handset. The
organisation form says so when the channel is open, rather than letting anyone
believe in a retention policy that only holds at home.

## A failed send never blocks a parcel

The caretaker has the parcel in hand. Recording it must not fail because an
occupant never gave a number, because the channel is closed, or because the
carrier refuses. The outcome, sent or the reason it was not, is logged as an
internal note on the parcel (`_message_log`), which needs no sender address: a
caretaker without an email address can still record a parcel. The notice goes
out once, at creation (the arrival is what interests the person), and editing
the parcel afterwards does not send again.

## Security

- **Consents**: the suite's Consultation group reads them; only the suite
  manager creates, edits, withdraws or deletes them.
- **Urgent send**: the authority guard is the first line of the method, before
  anything is read or sent. A resident calling it by RPC is refused before any
  text reaches the provider, since a message that has left cannot be recalled by
  a database rollback.
- The sending itself runs in `sudo()` under the line owner's identity; the
  consent lookup is the only way to a number.

## Dependencies

`bf_property_portal`, `bf_sms_archive`. Auto-install.

## Tests

19 tests: no message without a consent in force whatever the channel switch,
none with a closed channel whatever the consent, none with an open channel and
no line, a caretaker who is not on the line still sending, a withdrawal that
closes without erasing, a per-channel refusal, a carrier error reported cleanly,
the announcement's audience outranking the consent, an unpublished
announcement refused, no second send, a failing carrier that still records the
parcel, a message that carries neither door number nor carrier, consent only for
someone who lives there and once per organisation, no withdrawal before the
consent, and a resident unable to trigger the urgent send.

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
- **Change Date**: on 2030-08-22, this version converts automatically to
  **LGPL-3.0-or-later**.

## Acknowledgements

Created and maintained by Les services de consultation Blue Fox, Inc. AI coding
assistants were used as productivity tools during development.
