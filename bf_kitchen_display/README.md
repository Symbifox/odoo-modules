# Symbifox: kitchen display (`bf_kitchen_display`)

The waiter takes the order at the table on Odoo's register and presses Order: the dishes reach a
kitchen screen, on the station that makes them. Odoo's own kitchen screen is part of its paid
edition; this one runs on the community edition, with `bf_kitchen`'s recipes and allergens.

## Features

- **Stations**: the hot line, the cold line, the bar. A station shows the dishes of its register
  categories (all of them when none is set), sent from its registers (Kitchen > Configuration >
  Kitchen stations).
- **Tickets**: each press of Order sends what changed since the last one: the dishes added, with
  their notes and allergens, the table, its covers, who sent it and how long ago. A dish added
  later is a new ticket; a note added or changed after the dish was sent (an allergy) reaches the
  kitchen, the old one struck out; the order's note changed shows on its open tickets, marked.
- **The cook moves a ticket along**: to do, in progress, ready, served (each time stamped); one
  step back for a press made by mistake.
- **Tables**: an order moved to a free table keeps its tickets, now on the new table; moved onto a
  table that has an order, the register hands its tickets to that order, in their state, and its
  dishes are not sent twice (served ones included). A bill split sends nothing new and cancels
  nothing. A station of a register category also takes its sub-categories; services are not sent.
- **Cancellations**: an order deleted after it was sent, or dishes taken off after they were sent
  (in a ticket of their own), show in red; the cook decides, once for all the stations that took
  the dishes of that press of Order: **Loss** (the dishes were started: they leave as their
  ingredients, from the kitchen warehouse's stock, reason *Cancelled after it was sent*, valued at
  the kitchen cost of the day) or **Not started** (no loss).
- **The register sends at once**: without a printer, Odoo sends Order's changes to the server only
  when the waiter goes back to the floor plan; with a station, it sends them at once.
- Kitchen > Service > Kitchen tickets keeps the history: sent, ready and served times by station.

## What the module does not do

- It takes nothing out of the stock for a dish served: the register does, at payment.
- The waiter's register is not told that a ticket is ready (the cook calls it).
- It has no courses (starters first, then mains): hold a dish on the register, or use a note.
- It does not print: Odoo's order printers keep working beside it.
- A combo's own line is not sent: its dishes are, as their own lines.

## Requirements

Odoo 18 with Point of Sale and its restaurant (`pos_restaurant`), and `bf_kitchen_pos`. The interface is in English with a French (Canada) translation.

## Changelog

- 18.0.1.0.0: first release.

## Licence

Business Source License 1.1 (see `LICENSE`). Each version converts to LGPL-3 on
its Change Date: 2030-11-01 for this one. In the meantime, use in production for
your own internal business operations is permitted.
