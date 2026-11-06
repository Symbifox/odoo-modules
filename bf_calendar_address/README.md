# BF Calendar — the address comes from the address book

A meeting with a customer gets the customer's address as its place, taken from
the address book, on one line a phone can turn into directions. The picker
beside it lists the other addresses on file — the head office, the plant that
is not the head office.

## Why

Odoo neither fills nor suggests the place of a meeting. So the address was
copied from the CRM into the calendar entry by hand, one customer at a time,
and the confirmation to the customer went out through another calendar
altogether, because the Odoo invitation did not carry the address.

Two things were asked for by name, and they are what shapes this module:

- the place has to be **usable on a phone**, read between two plants, tapped to
  open the GPS;
- the place has to be **a suggestion**, never an imposition: an invitation that
  says the head office when the visit is at plant 3 is worse than an empty one.

## How it works

- **An empty place fills itself in** when a guest who is a customer joins the
  meeting — on screen as the guest is added, and on the server when the meeting
  is created from a booking link or opened from a contact's form.
- **The address is the customer's company's**, or the guest's own when they are
  filed on their own. It is written on one line, in the order and spelling of
  the country's own address format:
  `128 rue des Forges, Sherbrooke QC J1H 4M2`. The company's own country is
  left off; any other country stays.
- **Address on file** (the picker, under the place) lists every address the
  guests have on file: each guest, their company, and the other addresses filed
  under that company, each shown with the address itself so the choice can be
  read. Picking one writes it as the place.
- **We only ever fill in an empty place.** A place that was typed, picked from
  the list, carried by a booking type, or supplied by a default of the screen
  you opened is never replaced. An empty place with a contact still named
  beside it is not filled in either: somebody cleared it on purpose.
- **The place we filled in leaves with the contact who supplied it.** Remove
  that guest and the place goes — whichever order the guests come and go in. An
  address left behind by a guest who is no longer invited is a place nobody
  checked, and it would go out in the invitation to whoever is still on it.
- **An online meeting is left alone**, and a meeting that becomes online later
  gives the address back: a video call is not somewhere you drive to.
- **A booking that syncs itself does not wipe the place.** Odoo's booking
  module rewrites its meeting on every change and pushes the booking type's
  place, which is often empty; the address we filled in is put back.
- **Nothing is read from outside the address book.** No address is guessed from
  an email domain, and none is fetched from a website.
- **Our own addresses are not destinations**: the companies on this database,
  anyone filed under one of them, and anyone who signs in as an internal user
  are never suggested. A portal user is a customer and is.
- **An invoicing address is offered but never chosen on its own**: a PO box is
  a real address and a poor place to meet. A delivery address is chosen — that
  is the loading dock, and someone is there.
- The place goes out in the invitation (`LOCATION` of the `.ics`), which is
  where it is actually read: in the phone's own calendar. On a meeting that has
  a video call, the booking module puts the link there instead.

## What it does not do

- It does not rewrite the place of meetings that already exist. Installing it
  changes nothing about what is already in the calendar.
- It does not fill anything in for an event mirrored from another calendar, or
  for a caller that passes an empty place rather than none at all — that is
  somebody saying there is no place, not a place waiting to be found. A caller
  can also ask to be left alone entirely (`bf_address_skip` in the context).
- It does not follow guests added later by a program (an import, a script).
  Those write to the record without passing through the screen, and silently
  rewriting a place on a batch of meetings is not worth the one case it saves.
- It does not update a place after the address is corrected in the address
  book. The meeting keeps the text it was given — and from then on we no longer
  recognise it as ours, so we no longer clear it either.
- With two customers on one invitation, the address book's own order decides
  which one is suggested, not the order the guests were typed in. That is one
  more reason it is only a suggestion.
- It does not turn a physical address into a link inside Odoo. On a phone, the
  calendar entry already does.
- It does not appear in the calendar's quick-create dialog, which has no place
  field of its own: add the guest there and the address arrives when the
  meeting is saved.
- Archiving a contact removes them from the guests of their meetings — that is
  Odoo, not this module — and the place we filled in leaves with them. On the
  server, where an archived guest is still on the record, the place stays.

## Fields

| Field | What for |
|---|---|
| `bf_address_partner_id` | The contact whose address is the place. The picker, and how we know the place came from the address book. |
| `bf_address_candidate_ids` | The addresses on file for the guests (what the picker offers). Computed, and read as the user: a contact hidden from this reader is not offered. |

## Requirements

`calendar`. Nothing else: the address book is core Odoo.
