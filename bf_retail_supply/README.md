# Retail suppliers and orders (`bf_retail_supply`)

The days a supplier delivers, the order guide that fills itself, the price file of the
wholesaler, and what keeps coming back.

Lot 2 of the retail suite, on top of `bf_retail`.

## What it adds

- **On the supplier**: the weekdays it delivers, the hour to order by, the lead time, the
  minimum order, a van to the store or a depot to collect from, the representative, and
  whether it credits what did not sell.
- **An order guide that stays.** Built over a day, aisle by aisle, it keeps every quantity
  typed by hand and refreshes the facts around it. The kitchen's order guide is a wizard
  that dies with the screen; a store's does not.
- For each item: on hand, forecast at the delivery after the next one, already ordered on
  an unconfirmed request, the minimum and maximum of its reordering rule, the quantity
  suggested rounded up to the item's purchase unit, the supplier's price, **the cost of the last
  delivery and how much it moved**, and what came back as a credit over three months.
- **Odoo's own request for quotation**, in the item's purchase unit, for the delivery day
  at noon in the store's time zone. An item whose supplier has no price in force — an
  expired vendor line, or none — is **named as such** rather than shown at 0.00, it is left
  out of the total, and no request goes out on it: absent is not zero.
- **The wholesaler's price file**, CSV or Excel, read whatever its column names, its
  separator or the sheet it sits on, matched on the barcode then the internal reference
  then the supplier's own code. Each new price **opens a new vendor line dated from the
  file** and closes the one before it, carrying over everything that line held: the
  minimum order, the lead time, the sequence, the currency, the description. A vendor line
  written on the **model** is the template of every variant of it, and is only closed once
  the file covers all of them; a line with **no company** is left to the other stores; and
  the supplier, the attachment and the date are **frozen as soon as the file is read**, so
  what is applied is what was read.
- **Which column was read as what**, and what the rows were split on, in the chatter and on
  the file: a wholesaler who sends a retail price next to a net price sends two columns of
  prices, and "Prix de détail", "Retail price" and "Prix de vente" are never read as a
  purchase price.
- **A partly unreadable file applies what it can and names the rest.** A wholesaler sends
  300 rows with 2 broken ones; refusing the lot would stop the store. Every row is kept and
  counted, and the ones left out say why: price not readable, price of zero or less, row
  without a code, code with no item, unit to check, the same item twice. On screen, row by
  row, and in the chatter.
- **Two suppliers read per unit of the item**: a price quoted by the dozen says nothing
  next to a shelf that sells by the unit. ⚠️ In Odoo 18 the unit of a vendor line is a
  related field on the product's purchase unit, so all the vendors of an item share it: a
  supplier that sells in another pack size is modelled by its minimum quantity.
- **Credit notes for returns** recorded item by item, without touching the cost series.

## What it refuses to guess

- The unit of a price in a file. The price is applied in the unit of the vendor line
  already in Odoo; when the file's unit label disagrees with it, the row is left out and
  named. A case priced as a unit is a 24-fold error, and worse than no price at all.
  The labels are compared as **words**, not as pieces of words — "CASE OF 12 UNITS" holds
  the word "units" and is still a case — and against the **technical** unit, not its
  translated name, so the same file is read the same way in French and in English. The
  trade's own labels are known in both languages (EA, Each, Un, Unité, CS, Caisse, Doz,
  Dz...). A label nobody recognises is a doubt, not a pass, and no factor is ever guessed
  from one. The **column** counts too: "Prix caisse", "Case price" and "Case cost" hold the
  price of the case, so they raise the same doubt as a label that names one. And a measure
  written alone — "4 L", "750 ML" — is the **format of the item**, not the unit it is
  priced in: it raises nothing, where "12 x 1 L" and "12 UNITS" are containers.
- What a number means when the file does not say. "1,250" is 1.25 in French and 1 250 in
  English: the row is refused and named rather than divided by a thousand. The currency is
  read as noise around the number, in Canadian, American, European and **New Zealand**
  dollars alike.
- Whether a quoted price is a cost. The file writes the cost series only when asked, for a
  store whose supplier does not bill item by item.
- What a credit for returns does to the cost. It does not: it says order less.

## What it writes in the chatter

The reports this module leaves on a price file or on an order guide are **logs**, not
notifications: no email goes out and no bell rings, so a manager whose user carries no email
address can read a file and apply it like anyone else. And every body comes out in the
**language of the store**, not in the language of whoever set it off, because what the chatter
keeps is read later by someone else. The rule and its cost are the same as in `bf_retail`,
which holds the one helper both modules call.

License: Business Source License 1.1 (see LICENSE).
