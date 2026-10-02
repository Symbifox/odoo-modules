# Rent and arrears (`bf_rental_rent`)

Rent terms for a lease, the payments applied to them, and what is still owed.
Nothing more, and that is deliberate.

## What it does

| Model | Purpose |
|---|---|
| `bf.rental.term` | One rent term of a lease: due date, amount due, amount paid, balance, state (upcoming, paid, partial, late, deposited at the court office, after the end of the lease) and days late |
| `bf.rental.payment` | A payment received on a term: date, amount, method, and whether a receipt was given |
| `bf.rental.lease` (extended) | The lease's terms, the date rent is due until, its arrears total, its oldest delay in days, and whether the court may still grant time |

Terms are entered by the lessor; the module does not generate a schedule. By
default rent is payable on the first day of each term (art. 1903 para. 2 CCQ),
and the lease may agree otherwise.

- The **balance** of a term is the amount due minus the payments, never below
  zero. A term paid late has no delay any more: it has been paid.
- **Days late** count from the due date, and only on what remains owed.
- The lease's **arrears** are the sum of the balances of late and partial terms.
  An upcoming term is not arrears, and neither is a term deposited at the court
  office.
- **Rent due until** is entered on the lease once it has actually ended
  (resiliation, non-renewal, agreed departure): the last day the lease covers,
  say 30 June, not the day of departure. A term falling due after that date is
  shown as *after the end of the lease*: neither late nor arrears, even when it
  was paid, so that money held for a period no longer let shows. The term
  current at that date stays whole: the module computes no proration. What an
  occupant who stays on after the end owes is not rent, and the module does not
  track it. The lease's end date is not used for this: a fixed-term lease is in
  principle renewed by operation of law (art. 1941 CCQ). On an ended lease, the
  three-week flag is hidden: the recourse it describes concerns a lease in
  force.
- **"The court may still grant time"** is true while the oldest delay is three
  weeks (21 days) or less. See below for what that means, and what it does not.
- The **receipt given** box records the right of a lessee paying in cash to a
  receipt (art. 1564 CCQ).

The state and the days late are stored. They are computed when the term, its
payments, its deposit status or the lease's rent due until date change, and a daily scheduled action carries them
forward: an unpaid term becomes late the day after its due date, its days late
keep counting, and the three-week flag turns on its own.

## What the module refuses to conclude

**It never proposes resiliation.** Three articles that a naive reading runs
together. Art. 1971 CCQ opens the remedy: the lessor *may obtain* the resiliation
of the lease, which means *may ask the court for it*, never *may resiliate*.
There is no unilateral resiliation of a residential lease in Québec. A test holds
that no field suggests it.

**And three weeks is not a resiliation threshold.** Art. 1973 CCQ says what it
changes: when resiliation is sought, the court may grant it immediately or order
the debtor to perform within a period it determines, unless the rent is more than
three weeks late. The threshold removes the court's power to **grant time**.
Below it, the court may order performance instead of resiliating. It is a rule
about the **court's discretion**, not about the lessor's right. A module showing
"three weeks: you may resiliate" would state two falsehoods in five words; this
one shows whether the court may still grant time, and nothing else.

**And nothing is settled until judgment.** Art. 1883 CCQ: the lessee sued avoids
resiliation by paying, **before judgment**, the rent owed, the costs and the
interest.

**It computes no interest.** The rate is the one set under s. 28 of the *Tax
Administration Act*, fixed by regulation and revised quarterly: data published
elsewhere and dated, like the base percentage for rent fixing or the income
thresholds of the Société d'habitation du Québec. The module carries no interest
field, and a test holds that absence.

**It does not count late payments to draw a conclusion.** Frequent lateness opens
the remedy only where the lessor **suffers serious injury**, which the text does
not quantify. Proposing anything on the *n*-th late payment would invent a
threshold the law never wrote, the same fault as the "legal rent increase
percentage" that does not exist.

**It holds no total balance of the lease.** Art. 1905 CCQ voids any clause making
the whole rent exigible on default. Acceleration is ordinary elsewhere and void
here; the field does not exist, and a test guards its absence.

## What it refuses to record

- A **term larger than the agreed rent** (rent plus services, as on the lease).
  Claiming the rest of the lease at once is without effect (art. 1905 CCQ), and
  exacting more than one month's rent in advance is forbidden (art. 1904 para. 1
  CCQ).
- A **payment of zero or less**. A correction is made by editing the payment,
  not by adding a negative one that would falsify the history.

## Unpaid is not in default

Art. 1907 CCQ lets the lessee, with the court's authorisation and after ten days'
notice, deposit the rent **at the court office**. They have paid, elsewhere. A
term marked as deposited takes the "deposited" state and never counts in the
arrears. A module counting everything uncollected as arrears would accuse
someone of doing exactly what the law allows.

## Security

| Group | Terms and payments |
|---|---|
| Consultation | Read |
| Manager | Read, create, write, delete |

Multi-company record rules on terms and payments: who owes what to whom is as
sensitive as the lease itself. Records with no company stay visible, as
everywhere else in the suite. No mail template. One scheduled action, which only
recomputes the state and the days late of terms past their due date.

Menu: *Rent*, under the people menu of the property suite.

## Dependencies

- `bf_rental`

## Tests

30 tests, covering the term states and balances, the arrears and the three-week
flag, the daily refresh, the end of the rent (its last day, clearing it, a paid
term after it, a copied lease, and the renewed lease whose end date alone does
not stop it), the refusals (no resiliation field, no total balance, no interest, no term
above the agreed rent, no negative payment) and the multi-company wall.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations, which include administering immovables that you own or
  that you are constituted to administer, and letting a person acting on your
  behalf use your instance for that purpose. A lessor running the module for its
  own building is covered, and so is the bookkeeper or the manager it hires who
  works inside its instance.
- **Requires a written agreement**: administering immovables for the account of
  others, and providing the module as a product or service to third parties,
  whether hosted, managed or resold.
- **Change Date**: on 2030-08-30, this version converts automatically to
  **LGPL-3.0-or-later**.

## Acknowledgements

Created and maintained by Les services de consultation Blue Fox, Inc. AI coding
assistants were used as productivity tools during development.
