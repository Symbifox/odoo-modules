# Symbifox Property Showings (`bf_appointment_visit`)

Showing appointments for real estate: the seller's availability crossed with the
broker's, a visit register that meets Québec's professional obligations, and an
approval loop the seller can use from an e-mail, without an account.

Satellite of *Symbifox Appointment*. It needs `bf_appointment` **18.0.2.59.0** or
later: it uses `_bf_candidate_slots(combination=...)` and `slot_capacity`, both
opened in that version.

## Why this module exists

A generic scheduler can already intersect two calendars. What no scheduler on
the market carries is what a Québec broker must do around the appointment:

- **The visit register.** The OACIQ asks the broker to verify visitors' identity
  and record their names in a visit register, for the security of a property
  that is being shown to strangers.
- **The representation question**, asked at the first opportunity: is this
  person already represented by a broker? The answer bears on the *efficient
  cause of the sale*, which is what remuneration disputes turn on. Asked at
  booking time and time-stamped, it becomes evidence.
- **The occupied dwelling.** Article 1931 of the Civil Code of Québec requires
  24 hours' notice and a visit between 9 a.m. and 9 p.m., and the tenant may ask
  for the landlord to be present. It is public order: nobody can waive it in
  advance.

## Features

- **One listing builds everything.** Address, sellers, authorised brokers and
  offered windows; the module creates the booking type, the property calendar,
  the material resource and one combination per broker. Doing that by hand for
  every address is the real obstacle, and it is also where the silent trap sits:
  a booking type born on the company calendar (Monday to Friday) drops every
  weekend slot without a word.
- **The seller's windows live on the property's calendar**, the frame calendar
  on the type carries what the agency or the law imposes. A one-off window
  ("this Saturday, 1 to 4") is an attendance line bounded to that date, which
  the core already understands.
- **One combination per broker, never a K-of-N.** K-of-N accepts *any* K free
  resources, so `{broker A, broker B}` would pass without the property itself
  and the seller's windows would be ignored.
- **Approval loop**: automatic confirmation for a vacant property, seller (or
  broker) approval otherwise. The slot is held while the answer is pending, but
  the visitor is told the truth: the request is *transmitted*, not confirmed.
  The seller accepts, declines, or proposes another time from a token link.
- **Access instructions are released on confirmation only**, never on the public
  page.
- **The register closes when the visit starts.** Once arrival is recorded, the
  registry fields stop accepting writes for anyone but a manager, and a
  manager's correction is tracked. A document that can be retouched afterwards
  is worth nothing the day it matters.
- **Identity is recorded, never copied**: the kind of document seen, who checked
  it and when. No field can hold a document number.
- **Open house**: several people per slot through `slot_capacity`, plus a
  walk-in sign-in sheet behind a QR code for whoever shows up unannounced.
- **Feedback after the visit**, and an explicit switch before anything reaches
  the seller.

## Public pages

| Route | Who | What |
|---|---|---|
| `/appointment/<slug>` | visitor | the booking page, from `bf_appointment` |
| `/visite/vendeur/<token>` | seller | accept, decline, propose another time |
| `/visite/accueil/<token>` | walk-in visitor | sign-in sheet during an open house |
| `/visite/retour/<token>` | visitor | feedback after the visit |

All four are token-only and rate-limited.

## Optional bridge

A companion bridge module links a listing to a dwelling record from the Blue Fox
property-management range: address, occupant and occupancy come down from the
unit, and the unit gains a "show this dwelling" button. It stays a separate
module on purpose, because a broker has no property portfolio to speak of, and
a landlord has no use for a brokerage register.

## What it does not do

- **Lockboxes.** Supra and SentriLock have no public API and depend on the
  board. Promising integration would be promising someone else's roadmap.
- **Buyer tours.** Chaining several showings into one optimised route is a
  module of its own.
- **MLS synchronisation.** Listings are entered here or come from the bridge.
