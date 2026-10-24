# Symbifox École: child care billing (`bf_school_childcare_billing`)

Bills the child care of `bf_school_childcare` each month to the adults who pay, on top of the
school billing (`bf_school_billing`) and the Relevé 24 (`bf_school_rl24`).

## Features

- **Rates** per school and school year (Child care > Configuration > Rates), each one child care
  for the Relevé 24 or not, with an eligible part per unit when only some of it is child care
  (an outing day of 47 $ whose child care is 22 $):
  - occasional period, for one period or every period; or occasional day, one price a day
    whatever the periods booked (when the school has a day rate, it wins);
  - day without class (pedagogical day, open break);
  - child not booked (a child who came without a booking nor a registration); without this
    rate, the occasional one;
  - short notice supplement (a booking made by the office within the notice);
  - late pick-up: a first block of minutes, then each block begun after it (not child care
    by default: a penalty);
  - book of tickets: a number of tickets and its price. A ticket pays one occasional period, or
    one occasional day when the school prices by the day.
- **Monthly tiers** for the regular registrations, by periods per week, when the school bills
  regular child care each month (a setting of the school; otherwise they are a fee of the
  contract for educational services and nothing more is billed here). The tier counts the
  periods of the class days (before school, lunch time, after school); a month begun or ended
  mid-way is prorated on its class days. The days without class of a regular registration are
  billed at the day-without-class rate, on the days they run.
- **Activity fee** on an activity of a day without class, billed to each child booked, not
  child care by default. Set by the Administration only.
- **The ledger of charges**: what is due, computed again from what happened (bookings, sheets,
  registrations) until it is billed, paid by a ticket or waived, and never changed after. A
  booking cancelled late stays due ("place reserved, place paid"); cancelled in time, closed by
  the school or by the calendar, nothing is due; a ticket spent on it comes back. Nobody writes
  a charge by hand; the office may waive a charge not billed yet, saying why. A billed mistake is
  corrected by a credit note. The values a charge is born with are written by the computation,
  never taken from the caller's context or own defaults.
- **Monthly billing** (Child care > Billing): on the first of the month, last month is billed
  by itself; a month can also be billed with its button once it is over. The billing tells what
  could not be priced (a missing rate or tier). Two billings of the same month at once are
  serialized. The books of tickets pay the occasional periods first, oldest book first. Then one
  invoice per adult who pays and per month served (a charge left open from an earlier month is
  billed under the billing of its own month, whose year the Relevé 24 counts), all the children
  on it, in their language, exempt from tax, dated on the school's clock, sent from the school
  with its PDF:
  - who pays: the family shares of the student's contract (shared custody, 60/40), else the
    adults ticked « Pays » in equal shares, else the first adult with parental authority who
    signs; nobody: the charges wait, and the school is told (a note, and an activity for the
    Administration person who billed);
  - each line split in its child-care part and the rest, tied to its child;
  - a payer without an email address is told to the school, never marked sent.
- **Books of tickets** on the family portal (child-care page): an adult with parental authority
  buys a book for the child, pays its invoice online (any payment provider; the official PDF is
  made at once), and sees the tickets left of the books they bought. A second click on « Buy »
  opens the book still to pay. Valid for the school year, not refunded. A payment undone (or a
  credit note) stops the book: the periods it already paid stay paid by it, its invoice is due
  again, and no ticket pays anything more until it is paid. The office cancels a book not paid at
  all, with its invoice.
- **Relevé 24**: `bf_school_rl24` also counts the child-care invoices. A month's invoice counts
  in the year of the month served, whenever it is paid; a book of tickets in the year it is
  paid. On each invoice, the money kept pays its lines in proportion; the child-care lines of
  each child count at that rate. Only its credit notes lower it, never below zero; nothing an
  accounting user adds to a credit note raises it. A company never gets a slip.

## Roles

- **Administration**: the rates, the tiers, the monthly billing, waiving a charge, cancelling a
  book not paid. It bills without the Invoicing group: the buttons check the right, then the
  billing creates the invoices as the system.
- **Child care manager**: reads the rates, the charges, the billings and the books.
- Educators and teachers read none of it.

Reserved fields (the run of an invoice, the child of a line, the child-care tick of a line of a
child-care invoice) are refused when a person writes them; a child-care invoice is not reset to
draft. Every portal route checks parental authority before reading anything in sudo; an adult
sees only the books they bought.

## What has not been confirmed

- No pilot school yet: the units follow what 18 private schools publish.
- No late fee on an unpaid invoice, no proration by week, no sibling discount on child care.
- Lunch-time supervision counts in the monthly tier, child care for the Relevé 24 with it: a
  school whose lunch is not child care bills it on the contract.
- A booking of a period whose rate changes after a month is billed keeps its price.
- A book of tickets is paid with the school's online payment providers; Moneris is not
  supported (see `bf_school_billing`).
