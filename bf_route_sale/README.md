# Symbifox Work Routes: Sales on the Road (`bf_route_sale`)

Sell from the truck at each stop of a [`bf_route`](../bf_route) route: the
truck's own stock, the invoice at the customer's price, deposits on returnable
containers, and the payment recorded on the worker's phone.

> **Not tax advice.** Taxes come from each product and the customer's fiscal
> position, as configured by your accountant (for instance, bottled water in a
> large container is zero-rated in Canada, while a dispenser rental is not).

## Features

- **Truck stock**: each vehicle gets its own stock location. *Load the truck*
  prepares a transfer from the warehouse for the usual quantities of the
  stops left; *Unload the truck* sends back what is left at night.
- **Usual products** per stop, with their quantity, copied to each route day.
- **At the stop**, on the phone: quantity delivered, empty containers taken
  back, an estimate of the amount with taxes, and how it was paid: **cash**,
  **cheque**, **card** on the worker's own payment terminal (the phone records
  the amount and the authorization number, never card data), or **on
  account**.
- **Marking the stop done** delivers from the truck, posts the invoice at the
  customer's price list, charges the deposit on each full container and
  credits each empty, registers the payment and reconciles it. More empties
  than deliveries makes a credit note.
- **Containers held** by each customer: a ledger on the contact, opening
  balances entered by hand.
- **Never loses a mark**: a mark made without network may arrive hours later.
  A payment that does not match the invoice or money handed over with a credit
  note is recorded and flagged on the day for the office, never refused. A
  delivery beyond the truck's recorded stock is recorded too: the truck goes
  below zero, and unloading corrects it with a note.
- **At the end of the day**: the cash and the cheques to hand in.

## Access

Route managers become stock users (they load and validate the transfers).
Invoices are visible to users with invoicing rights. The worker has no right
on stock or accounting: the documents are created by checked methods once the
day is confirmed to be theirs.

## Configuration

1. *Settings › Work routes › Payments on the road*: the cash, cheque and card
   journals.
2. On the products sold: *Sold on routes*, the *Container deposit* product and,
   if empties are stocked, the *Empty container* product.
3. On each vehicle: *Truck stock* (button *Create*).
4. On each stop of a route: its usual products.

## Sales the office confirms

The phone never invoices on its own word a product outside the stop's usual
ones (or archived, or not for sale): the sale is recorded as "to finish" and
the office confirms it with *Retry the sale*. Documents that cannot be made
(an account missing, for instance) give the same: the sale stays recorded,
the office finishes it. A credit note (more empties than full containers) is
left in draft for the office.

## Payments

The payment is recorded exactly as received. A cash amount equal to the
invoice rounded to the nearest 5 cents is the legal rounding: never reported,
and written off to the cash journal's difference accounts when the payment
makes a journal entry. Any other gap is reported to the office.

## Known limits

- No receipt printed or sent from the phone yet.
- The phone's estimate matches the invoice to the cent for simple percentage
  taxes (groups included) rounded per line, with the price list's quantity
  breaks; other tax setups fall back on unit prices with taxes.
- A sale "to finish" moves no stock until the office confirms it.
- Unloading follows the vehicle's stock: unload a day before the truck leaves
  again.

## License

Business Source License 1.1, see `LICENSE`.
