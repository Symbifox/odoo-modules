# Symbifox: host stand (`bf_kitchen_host`)

The host greets the guests and seats them; servers serve. This module gives the host a place on
Odoo's register: the floor plan, where each table is, the sections in turn, a waiting list that
quotes a fair wait, and the kitchen's pace. It runs on the community edition, beside
`bf_kitchen_display`'s kitchen screen.

## Features

- **The host's role**: an employee listed as a host on the register (Point of Sale settings, Host
  stand; the register must log in with employees, and the host must be one of its employees when that
  list is restricted) logs in with a PIN like any employee and sees the floor plan only: seat a party (covers,
  name, a note such as an allergy), change it, move it before it orders, mark the table cleared.
  The host never opens an order nor takes a payment; joining two tables by dragging one onto the
  other stays Odoo's own move, which merges their orders. Any other employee keeps every right of
  the register and seats from the same panel (**Seat guests**).
- **Each table's state** on the floor plan: free, seated, ordered, served (every kitchen ticket
  served), to clear (paid, not cleared yet), with its covers, its server and its section's colour.
- **A seating is kept**: Odoo forgets a table opened without an order; the seating keeps the party
  from the host's seating to the table cleared. A party seated without the host gets one with its
  first order. Its times give the length of a meal (Kitchen > Service > Seatings).
- **The server's order takes the party's covers and note**: the allergy noted at the door reaches
  the order, then the kitchen.
- **Sections in turn**: the floor is split in sections (Kitchen > Configuration > Host sections),
  one server each for the service, changed from the host's panel. The next section is the one
  whose server took the fewest covers in the last 4 hours of the session, then the longest without
  a table; a section seated within the double seating delay comes last. Its smallest free table that
  fits is proposed (joined tables count all their seats). Seating elsewhere is said: not its turn, a double seating (a section
  seated again within 10 minutes, set on the register), a table too small or much too large.
- **Waiting list**: name, covers, note; the wait quoted comes from the real length of the meals of
  that party size at this register (60 minutes until three meals are known) and from the tables
  that fit it, rounded up, plus 5 minutes: quote long, then beat it. A party is seated from the
  list (the wait it had is kept) or marked gone.
- **Pace**: the covers seated in the last 15 minutes against what the kitchen can take (set on the
  register; none by default), and the kitchen tickets still open with the oldest one. Green: seat;
  amber: slow down; red: hold the door.
- **Names and notes are kept for the service only** (Loi 25: a name is personal information, an
  allergy is health information): erased when the session closes, or after a day by a nightly job,
  from the seating and from the order, its refunds, its kitchen tickets and the receipts kept with it
  (sent by email), every note the seating had included. The notes never reach an invoice, whenever
  it is made. A receipt printed, or already sent by email, during the meal keeps them; the customer
  display shows them during the meal.

## What the module does not do

- No table booking (Odoo's is in its paid edition).
- It sends no text message to a waiting party: the host calls the name.
- A host and a server share the register's user: the host's limits are in the register's screen,
  like Odoo's own employee roles.
- An order moved onto a table that has an order joins that table's party; the party it left is
  marked gone. Moved onto a table where a party sits without an order yet, it takes the table.
- Two registers sharing a floor each keep their own seatings: give each register its own floors.
- Typing a number that is not a table in the register's bar makes an order without a table, as in
  Odoo, for the host too.

## Requirements

Odoo 18 with Point of Sale, its restaurant (`pos_restaurant`) and its employees (`pos_hr`), and `bf_kitchen_display`. The interface is in English with a French (Canada) translation.

## Changelog

- 18.0.1.0.0: first release.

## Licence

Business Source License 1.1 (see `LICENSE`). Each version converts to LGPL-3 on
its Change Date: 2030-11-01 for this one. In the meantime, use in production for
your own internal business operations is permitted.
