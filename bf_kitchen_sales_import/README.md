# Symbifox: kitchen sales from another register (`bf_kitchen_sales_import`)

For a kitchen whose register is not Odoo's (Square, Lightspeed, Toast, Clover): the item sales
export of that register takes the dishes' ingredients out of the kitchen stock (`bf_kitchen`),
one day at a time.

## Features

- **Registers.** One per source (a dining room on Square, a bar on Lightspeed), with the
  warehouse its sales take from and how its export writes dates (day/month or month/day; a
  date written 2026-04-03 is read in any case).
- **The file.** CSV (comma, semicolon or tab; UTF-8 or Windows) or Excel (first sheet). The
  header is found in the first rows by its names, in English or French: a date, a quantity, and
  an item code or name. A quantity with a decimal comma is read; a row that cannot be read is
  refused with its number.
- **Matching, once.** Each item of the register is matched to its dish (or a kitchen item sold
  as it is), or marked "Not food" (a gift card, a delivery fee). The match is kept for the next
  files of the same register; an item is recognised by its code, else its name, whatever the
  case or spacing.
- **Taking out.** One Kitchen consumption transfer per day, dated of that day: the dishes
  leave as their ingredients. The same file, or a file with a day already imported for the
  same register, is refused. An import is cancelled as a whole: the food goes back in stock and
  its days are free again, to import a corrected file.
- In the kitchen's variance report, these sales have their own column ("Imported sales").

## Rights

The kitchen manager. A sale of an import comes from its file: only its dish and "Not food" are
written by hand; its state moves with its buttons only.

## What the module does not do yet

No connection to the registers' APIs: one will be written per register a kitchen actually
uses. Refunds in an export are netted against the day's sales of the item (a day below zero takes
nothing out): unlike Odoo's register, a refunded dish is not a loss here.

## Requirements

Odoo 18 and `bf_kitchen`. Excel files are read with `openpyxl`. The interface is in English with a French (Canada) translation.

## Changelog

- 18.0.1.0.0: first release.

## Licence

Business Source License 1.1 (see `LICENSE`). Each version converts to LGPL-3 on
its Change Date: 2030-10-31 for this one. In the meantime, use in production for
your own internal business operations is permitted.
