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
- **Food allergies**: Health Canada's priority allergens, from the list shared with the
  kitchen (`bf_food_allergen`); the school manager adds others. A meal containing a declared
  allergy of the student cannot be ordered (with `bf_kitchen_school_meal`, the allergens of
  the meal's kitchen dish count too); the kitchen list opens on tomorrow's
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
  list is read in Symbifox or exported. A caterer who cooks in the same Symbifox links
  each meal to its dish with `bf_kitchen_school_meal`.
- No refund of a positive balance by the software: the office records it.
- The lunch supervision fee (public primary) and the daycare are not billed here.

## Changelog

- 18.0.1.1.0: the food allergens come from the list shared with the kitchen
  (`bf_food_allergen`). The update moves the school's allergens, the students' allergies and
  the meals' allergens to it (an allergen the school added keeps its names), and removes the
  school's own list.

## Licence

Business Source License 1.1 (see `LICENSE`). Each version converts to LGPL-3 on
its Change Date: 2030-10-31 for this one. In the meantime, use in production for
your own internal business operations is permitted.
