# Symbifox École: meals (`bf_school_meal`)

The caterer's menus and meal orders on the family portal.

## Features

- **Menus**: the meals with their price and allergens; one menu per school day,
  in a calendar.
- **Family portal** (**Meals**), for the adult who pays and the adults with parental
  authority: order for each child up to the school's deadline
  (2 days ahead by default), cancel until the hour set on the day (8:00 by default,
  school time zone). The office orders or cancels at any time before billing.
- **Two ways to pay**, as the school decides or as the family chooses:
  - **prepaid balance**: the family tops it up by an invoice it pays online (any
    payment provider) or that the office records as paid; a meal is taken from the
    balance when ordered, given back when cancelled; an order the balance cannot
    cover is refused, even when two are sent at once; a top-up is credited with what
    was actually paid, and taken back if the payment is undone;
  - **monthly invoice**: the meals of the month on one invoice, sent at the start of
    the next month to the adult who pays.
  The mode is frozen on each order: a family that changes mode does not change what
  it already ordered.
- **Food allergies**: Health Canada's priority allergens. A meal containing a declared
  allergy of the student cannot be ordered; the kitchen list opens on tomorrow's
  ordered meals, grouped by meal, and shows every allergy. A student's allergies are for
  internal users only (the portal reads them in sudo to refuse a meal).
- **Closed day** (storm, closure): every order is cancelled and credited at once.
- **The balance is a register**: movements are never edited or deleted; the office
  records adjustments only, with their reason (the other movements come from the orders
  and the top-ups). An order's price, state and invoice move only with its buttons,
  from its creation. An account stays with the adult who pays, and a day is closed only
  with its button, which credits the orders.

## Taxes

Meals served to students at school are exempt from GST and QST: the lines carry no
tax. The school's accountant confirms before going live.

## What has not been confirmed

- No link with the caterer's own platform (none offers a public API): the kitchen
  list is read in Symbifox or exported.
- No refund of a positive balance by the software: the office records it.
- The lunch supervision fee (public primary) and the daycare are not billed here.
