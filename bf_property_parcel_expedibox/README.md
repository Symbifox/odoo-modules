# Immeubles : avis de colis du casier Expedibox

A parcel dropped in a connected Expedibox locker records itself in the parcel
journal and notifies its recipient. Its pickup is recorded the same way. The
concierge types nothing.

This module is the listening half of the Expedibox integration.
`bf_property_access_expedibox` is the speaking half: it enrols a resident in
the locker when they arrive and removes them when they leave. Keep both, or
neither — a locker that does not know its residents cannot notify them.

## What it does

A notice tells Symbifox **which reservation to re-read**. What happened is then
read from the reservation itself, with the organisation's own application
token:

| The reservation says | Symbifox does |
|---|---|
| a pickup time | Records the hand-over, at that time. A pickup always wins over the drop-off that preceded it. |
| the drop-off expired | Posts to the parcel thread and raises an activity for the access point's owner. It does **not** decide the parcel was returned. |
| a drop-off time | Records a parcel for the resident, at that time, with the locker and door it sits in, and the carrier and tracking number the courier typed. |
| none of the above | Recorded in the notice journal, no effect. A booked door is not a delivered parcel. |

The event name in the notice is kept for the record and never decides
anything. A notice whose name this module does not know, on a reservation that
carries a drop-off, still records the parcel: the reservation is the truth.

## Setting it up

1. On the Expedibox connector (Property ▸ Access ▸ Connectors), fill the
   organisation id Expedibox sends in its notice header, pick the person who
   receives unclaimed notices, then press **Nouveau jeton d'écoute**.
2. Copy the address the form then shows into the Expedibox developer portal,
   field *Reservation Webhook URL*, for your application.
3. Leave **Le casier avise lui-même le résident** checked unless your locker is
   configured to send nothing. Unchecked, Symbifox sends the text message
   itself through `bf_property_sms`, with the resident's consent.

A connector with a listening token but no organisation id, or no owner for
unclaimed notices, is refused on save: both are part of how a notice is
checked.

## Security, and why it is shaped this way

An Expedibox reservation notice **is not signed**. Their specification
(`x-webhooks: reservation_event`) defines no shared secret, no signature and no
signed timestamp. So:

- the listening address carries a 32-byte secret, compared in constant time;
  an unknown token gets a 404, never a 403;
- the `X-Expedibox-Organization-Id` header is checked against the connector;
- **the posted body decides nothing — not even the verb.** Three values are
  read from it: the event name (kept, never acted on), the timestamp, and the
  reservation reference. What actually happened is re-read from
  `GET /reservations/{id}/?include_qr_code_src=false` with the organisation's
  own application token, and the locker id used is the one that re-read
  returns. Without this, anyone holding the listening address could mark a
  parcel "handed over" while it still sat in the locker, or have a drop-off
  that never happened recorded and notified;
- **secrets are compared as bytes**: `hmac.compare_digest` raises on non-ASCII
  strings, which turned an accented address into a 500 — and a 500 only where a
  connector listens is exactly the answer the 404 refuses to give;
- **a body with no declared length is refused (411)**: a chunked body would
  otherwise be read before it could be measured;
- the resident is recognised **only** by the reconciliation identifier this
  software wrote into the locker (`sfx-<database>-p<partner>-pt<point>`), never
  by name, email or unit: those are fields anyone can type at a locker;
- a connector receiving more than 120 notices a minute gets a 429 and nothing
  is written;
- the route replies as soon as the notice row is committed. It reads nothing
  back, calls nobody, and touches no parcel: a scheduled pass (five minutes,
  plus an immediate trigger) does the work.

## Privacy (Québec, Law 25)

What never enters the database: deposit, pickup and admin pickup codes, the
door unlock code, the three base64 QR images, deposit and withdrawal photos,
and the email and mobile number the locker holds. The notice journal keeps a
reservation reference, an event name, two timestamps and a locker id; once
reconciled it also links the resident and the parcel.

A deposit that matches no resident enrolled by this software raises an activity
that names the locker and the door **only**, and only for an event that could
have had an effect: a building where twenty residents were enrolled by hand
would otherwise collect five tasks per parcel. The reservation does carry that
person's name, email and mobile: this software has no basis to keep them, so it
does not. A resident whose access right has been removed is no longer
recognised, and no exception text — which would name them — ever reaches a
field or a log.

The notice journal purges after 90 days, or sooner when the point's
organisation has set a shorter retention for its logs (`log_retention_days`).
Unprocessed notices are never purged — that would lose a parcel — and neither
is a notice handed to someone whose task is still open: deleting it would take
the task with it.

## What it needs

- `bf_property_portal` (the parcel journal) and `bf_property_access_expedibox`
  (the connector, the locker id on the access point, the API client).
- A publicly reachable Odoo: Expedibox must be able to POST to the listening
  address.
- Python `requests`.

## Licence

BUSL-1.1 — see `LICENSE`. Change Date: 2030-11-20.
