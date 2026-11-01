# Buildings: Lobby Tablet (`bf_property_lobby`)

A tablet or touch screen at the building's entrance stands in for an intercom
directory. The visitor searches for a name or a unit number and touches
**Call**; the resident's phone rings through Symbifox telephony (`bf_softphone`,
Asterisk) when it is configured, otherwise the person responsible for the
tablet is notified. A **Delivery** button notifies the person responsible.

The person responsible always gets an activity in Odoo (once per request, not
once per tap). With **Also by email** ticked on the tablet's record, they also
get an email in their own language, signed by the company, naming the unit and
never the resident.

The door does not open from here: the tablet calls, and the resident opens with
the intercom already in place. No biometrics, no camera.

## What it adds

| Where | What |
|---|---|
| `bf.property.lobby.kiosk` | A tablet: building, person responsible, call mode (notify, or ring the resident's phone through the tablet's SIP channel and a dial context), optional email to the person responsible, pairing. |
| `bf.property.lobby.call` | The call journal: which tablet, which resident, call or delivery, outcome. Purged after the organisation's retention period. |
| `bf.property.access.resident` | Whether the resident appears in the lobby directory, the label they choose, whether the tablet may ring their phone, and when they consented. |
| Portal, "My access" | One card per building that has a tablet: appear in the directory, label, reachable. Given and withdrawn by the resident. A manager can only *remove* a resident, at their request, never list them. |

## Privacy (Québec Act 25)

- **Nobody appears by default** (s. 9.1). The resident chooses to appear, with
  the label they choose ("A. Example", "Example family", or nothing but the
  unit), and whether their phone may ring. Leaving the directory also stops the
  phone from ringing.
- **No number ever leaves the server**: the tablet only sees labels and unit
  numbers.

## Security

- The tablet page is public and has no session. It is protected by a device
  token shown **once** at pairing; only its SHA-256 digest is kept, readable by
  system administrators only, compared in constant time. Pairing again or
  unpairing invalidates the previous address.
- Everything the page can do goes through private methods called after the
  token check: the directory of present residents who consented, a call to one
  of them, a delivery.
- Calls are capped (6 a minute per tablet, one call per resident every two
  minutes) so a public page cannot flood a phone or the person responsible. The
  cap holds under concurrent requests: the tablet's row is updated before
  counting, and the request that loses the race answers "One moment" (the
  neighbouring request just went through).
- The tablet only dials ordinary Canadian and US numbers (strict North American
  plan, no Caribbean area codes, no premium 900/976): the resident sets their
  own number on the portal, and the tablet must never dial a number that costs.
- The consent is the resident's: nobody writes it on their behalf, managers
  included. The person responsible's activity names the unit, never the
  resident, and activities and their messages are purged with the journal.
- ⚠️ The token travels in the page address: it can appear in the access logs
  of the reverse proxy. Keep those logs short-lived, and pair the tablet again
  if they leak.
- The portal consent form only changes the person's own resident records in a
  building that has a tablet.

## Dependencies

`bf_property_access_portal` (and through it `bf_property_access`,
`bf_property_portal`). `bf_softphone` is optional: without it, calls notify the
person responsible.

## Tests

Tests: directory limited to consenting residents and never showing a number,
wrong token or archived tablet not found, call that notifies without a phone
line, throttling, residents not listed or in another building refused, consent
given by the resident on the portal and never on someone else's record,
reachability and number cleaning, pairing reserved to managers with an
unreadable digest and the old address revoked, journal purge, concurrent
requests answered "one moment" instead of being lost, the optional email (once,
unit only, signed by the company), answers in the company's language whatever the
browser asks for, and the strings of the public page available for translation.

## Licence

Business Source License 1.1, see [`LICENSE`](LICENSE). Change Date 2030-11-01,
then LGPL-3.0-or-later. Same grant as `bf_property_access`.

## Acknowledgements

Created and maintained by Les services de consultation Blue Fox, Inc. AI coding
assistants were used as productivity tools during development.
