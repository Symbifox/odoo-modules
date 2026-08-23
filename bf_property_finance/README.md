# Co-ownership — Common expenses and fund calls (`bf_property_finance`)

Annual budget, allocation of common expenses, fund calls, collection and
arrears for a Quebec *syndicat de copropriété divise*.

## What it does

| Model | Purpose |
|---|---|
| `bf.property.budget` | The annual budget, its lines by expense type, and the comparison against what was called and collected |
| `bf.property.fund.call` | A call for contributions and its per-fraction lines |
| `bf.property.payment` | A payment received, imputed under arts. 1569 to 1572 |
| `bf.property.charge.statement` | The statement of common expenses due, art. 1069 al. 2 |

## Three things the common commentary gets wrong

**Article 1064 sets three regimes, not two.** Maintenance and *current* repairs
of a restricted-use common portion are borne by the co-owners who have the use
of it. Major repairs and replacement follow the general rule and are spread
over **all** the fractions: al. 2 says the declaration *may* provide otherwise,
so absent a clause, the whole immovable pays. Redoing the waterproofing of a
private terrace is not for its beneficiaries alone. A module with a single
"restricted use" expense type is wrong by tens of thousands of dollars.

**The meeting does not adopt the budget.** Article 1072: the board fixes it
*after consulting the meeting*. The consultation is a prerequisite, not a vote.
The notice is transmitted *without delay*, with no number of days in the text.
A special contribution has its own consultation (art. 1072.1).

**The contingency fund floor has three bases, not one.**

| Floor | Base | Who | Source |
|---|---|---|---|
| 0.5 % | reconstruction value of the immovable | the **promoter**, until they obtain the study | art. 1071 al. 4 C.c.Q. |
| 5 % | contributions to common expenses | any syndicat covered by the transitional regime, until the sums are fixed after its first study | Loi 16, art. 153 al. 2 |
| — | the study's recommendations | any syndicat once the study is obtained and the sums fixed | art. 1071 al. 3 C.c.Q. |

The 5 % floor is widely described as abrogated. It left the Code, but it is in
force in the transitional sections of the statute. When the module cannot tell
which regime applies, it says "basis unknown" rather than picking one silently.

## Collection is not a matter of preference

The imputation of a payment is set by the Code, not by the manager. Article
1569: the debtor states which debt they are paying, and may not pay in advance
over a debt already due without the creditor's consent. Article 1570: interest
before capital. Article 1571: an accepted imputation is not redone. Article
1572, **and only where the debtor gave no indication**: debts due first, oldest
first, and proportionally between those due on the same day, hence to the cent.

⚠️ The second paragraph of art. 1572, "the one the debtor has most interest in
paying", is **not applied and will not be**. That is a judgement, not a
calculation. The module says so on screen and lets the user impute by hand.

**Interest runs from default, never from the due date** (art. 1617, with arts.
1594 and 1595). No rate is hard-coded: the legal rate does not come from the
Civil Code, so the syndicat enters its own, with a default of no interest at
all. Interest is computed period by period on the capital outstanding then, not
on today's balance over the whole span.

## Allocation to the cent

Largest-remainder method, basis by basis and never on the total, since the
bases differ. Ties are broken on the lowest key so that a recomputation returns
what was already sent out. And four calls of 25 % do not add up to a financial
year that does not divide by four: the budget shows what remains to be called
rather than quietly spreading the cents.

## Deprivation of the right to vote

Article 1094 deprives the co-owner who, **for more than three months**, has not
paid their share of the common expenses. Three months exactly deprive nobody,
and the deprivation strikes the **person**, not the fraction: someone holding
three fractions and letting one lapse loses all their votes.

⚠️ Nothing ticks itself. The module computes the fact and proposes it; a button
applies it, with a trace in the chatter, because a payment received yesterday
and not yet entered is enough to make it fall.

Not to be confused with the 30 days of art. 2729, which open the legal
hypothec. That is outside this module.

## Statement of common expenses due (art. 1069 al. 2)

🔴 This is the one deadline in the suite that runs **against** the syndicat:
past 15 days, the acquirer is no longer bound and the claim must be pursued
against the seller, who has often gone. The statement therefore states its own
effect. Prior notice to the owner is a condition of the authorisation, not a
courtesy: without it the statement is refused. The total includes interest
(al. 1, "with interest"), and a statement once provided is not recomputed.

## Budget against actual

The module keeps **no expenses**: no invoice, no supplier, no ledger, and no
dependency on `account`. "Budget against actual" therefore means the cycle of
art. 1072: fixed, called, collected. The printed document says so in as many
words. Collected means **capital**, since interest funds no line item.

Article 1087 requires six documents with the notice of the annual meeting. The
module produces one of them and half of another, and the printed list says for
each whether it produces it, so that a board does not turn up without a balance
sheet. It blocks no convocation.

## Dependencies

`bf_property_core`, `bf_property_governance`. Deliberately **not** `account`.

## Tested

123 tests on staging: the three regimes of art. 1064, allocation to the cent,
the three contingency floors and the ten-year catch-up, the self-insurance
formula, imputation under arts. 1569 to 1572, interest from default, arrears
and deprivation, the statement of charges due, and budget against actual.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations, which include administering immovables that you own or
  that you are constituted to administer, and letting a person acting on your
  behalf use your instance for that purpose.
- **Requires a written agreement**: administering immovables for the account of
  others, and providing the module as a product or service to third parties,
  whether hosted, managed or resold.
- **Change Date**: on 2030-08-22, this version converts automatically to
  **LGPL-3.0-or-later**.

## Acknowledgements

Created and maintained by Les services de consultation Blue Fox, Inc. AI coding
assistants were used as productivity tools during development.
