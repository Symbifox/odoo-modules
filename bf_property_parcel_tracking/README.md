# Immeubles : suivi des colis chez le transporteur

A resident announces, from the portal, the parcel they are waiting for — the
carrier and the tracking number from their confirmation email. Symbifox asks
the carrier where that parcel is, and the day the carrier answers "delivered",
the parcel moves to held in the parcel journal and the resident is notified.

## What these APIs can and cannot do

UPS, DHL and Purolator answer **about a tracking number**. None of them will
say "here is what was delivered to 123 Main St today". A parcel therefore
cannot appear out of nowhere: someone has to supply the number — the resident,
who has it in their confirmation email, or a connected locker
(`bf_property_parcel_expedibox` stores the carrier and number the courier
typed).

Say this plainly to a client who expects magic. The locker integration is the
half that runs by itself; carrier tracking is the half that follows a number.

## The three carriers

| Carrier | State | What was read at the source |
|---|---|---|
| **UPS** | Connected | UPS publishes its OpenAPI specs (`github.com/UPS-API/api-documentation`: `Tracking.yaml`, `OAuthClientCredentials.yaml`), read 2026-11-19. Token: `POST /security/v1/oauth/token`, Basic auth, `grant_type=client_credentials`, optional `x-merchant-id`. Tracking: `GET /track/v1/details/{inquiryNumber}`, mandatory `transId` and `transactionSrc` headers, 7–34 character numbers, hosts `wwwcie.ups.com` (test) and `onlinetools.ups.com` (production). |
| **DHL** | Connected | Measured against the API itself (their portal rejects non-browser clients): `GET https://api-eu.dhl.com/track/shipments?trackingNumber=…` → 401 `application/problem+json`, and the server's own `access-control-allow-headers` names **`DHL-API-Key`**. Errors are RFC 7807. |
| **Purolator** | Declared, not connected | Their WSDL returns 401 and their documentation lives behind an account. The portal's own strings describe the key chain: development key (self-service, after their developer agreement) → certification key → automated certification → production key, which needs a Purolator **business account number** and stays **inactive until three people** from the business create Purolator accounts. |

### Why Purolator is not implemented

No SOAP envelope was written from third-party examples. A guessed message
shape would have produced a module that looks finished and is not — the worst
outcome for a carrier a client believes is connected. The account record
exists, its fields exist, and a tracking attempt refuses with the exact reason
and what to do about it. A Purolator number is followed by hand, and the
parcel record says so.

## What concludes "delivered", and what never does

- **UPS**: only a `deliveryDate` entry of type `DEL` — the one delivery signal
  their specification documents ("DEL - Delivered Date"). The vocabulary of
  `currentStatus.type` and `statusCode` lives in a portal appendix that refuses
  non-browser clients: it is displayed in UPS's own words and kept raw for the
  operator, and it decides nothing. A scheduled or rescheduled date (`SDD`,
  `RDD`) means in transit, not delivered.
- **DHL**: only `shipments[].status.statusCode == "delivered"`, exactly. Their
  status vocabulary was not readable at the source, so it is treated as an open
  list: any other value — known or unknown — leaves the parcel at "no answer
  yet", with DHL's own wording shown.

Concluding on a guessed list would mean telling a resident their parcel arrived
when it did not.

## What a resident may announce

A resident announces **their own** parcel, for **their own** unit. The portal
grant covers creation and the removal of their own announcement, nothing else,
and the server sets the recipient, the unit, the building and the organisation
itself: the whitelist says which fields an occupant may fill, not what they may
fill them with. A unit that is not theirs is refused, not silently replaced.
Announcements are capped per person per hour, and one tracking number is
tracked once per company — otherwise a few thousand announcements from one
account would take the whole query queue and the neighbour's parcel would never
be queried.

## The lifecycle

1. The resident announces: the parcel is created **Annoncé**, with the carrier
   and number. No text message goes out — "a parcel has arrived" would be
   false.
2. Symbifox asks the carrier, on the parcel's own schedule: 30 minutes while
   out for delivery, 2 hours in transit, 4 hours after a label was created.
3. The carrier says delivered → the parcel becomes **En dépôt**, dated at the
   carrier's delivery time, and the parcel journal's notice fires (the text
   message to a consenting resident, through `bf_property_sms`).
4. The concierge can also press **Arrivé** when the parcel is on the counter
   before the carrier admits it.

The notice is marked and committed **before** it is sent, outside the parcel's
savepoint: a text message that has left cannot be recalled, and a rollback
after the send would otherwise have it sent again on the next pass.

Carrier times are converted from the record's timezone to UTC. UPS returns a
local time with no offset for a delivery: an 18:15 delivery in Montréal was
being stored, and shown to the resident, as 13:15.

Tracking stops, saying why, when: the carrier does not know the number after
six tries (probably mistyped), the parcel was announced more than 30 days ago,
the key is refused, or the carrier is not connected.

## What protects the quota and the bill

Each account carries an hourly ceiling (60 by default). Beyond it, queries are
**postponed by fifteen minutes**, never dropped. Set it to zero only under a
contract that allows it.

## Privacy (Québec, Law 25)

A tracking number and a delivery's steps say what someone receives, and from
whom. This module keeps the **current state, its label and its date** — not the
route, not the signature, not the delivery photo: it never asks for them
(`returnSignature` and `returnPOD` stay "false" at UPS). The number disappears
with the parcel record. An announcement left without an outcome has no closing
date, so the journal's own purge would never see it: this module purges those
itself, on the organisation's retention, and the resident can remove theirs at
any time.

A resident may only announce **their own** parcel: the portal grant covers
creation only, for records whose recipient is themselves, and a portal create
has its recipient and its state forced by the server — the whitelist says which
fields an occupant may fill, not what they may fill them with.

## What it needs

- `bf_property_portal` (the parcel journal and the resident portal).
- `bf_property_sms` is optional, but **if it is installed it must be at least
  18.0.2.3.0**: older versions notify on creation, so a resident would get "a
  parcel has arrived" the moment they announce one still in transit. The
  install refuses rather than let that happen, and says what to upgrade.
- Keys: a UPS application (developer portal, linked to a UPS account) and/or a
  DHL API key. Nothing is needed to record an announced parcel by hand.
- Python `requests`.

## Licence

BUSL-1.1 — see `LICENSE`. Change Date: 2030-11-20.
