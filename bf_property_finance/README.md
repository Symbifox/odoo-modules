# Co-ownership: common expenses and fund calls (`bf_property_finance`)

Annual budget, allocation of common expenses, fund calls, collection and
arrears for a Quebec syndicate of divided co-ownership.

## What it does

| Model | Purpose |
|---|---|
| `bf.property.budget` | The annual budget, its lines by expense type, and the comparison against what was called and collected |
| `bf.property.fund.call` | A call for contributions and its per-fraction lines |
| `bf.property.payment` | A payment received, imputed under arts. 1569 to 1572 CCQ |
| `bf.property.charge.statement` | The statement of common expenses due, art. 1069 para. 2 CCQ, and the prior notice to the owner |

It also adds to the syndicate the data that drive the contingency fund, the
catch-up period, the self-insurance fund and interest on arrears, and to the
general meeting its attendance-level arrears and the list of documents that
accompany the notice of the annual meeting.

Three scheduled actions run daily: one refreshes which contributions are in
default (default arises from the passing of a date, not from an entry), one
refreshes the fifteen-day clock of each statement of charges due, and the third
carries the contingency fund's dates forward (see below).

## Three things the common commentary gets wrong

**Article 1064 CCQ sets three regimes, not two.** Maintenance and *current*
repairs of a restricted-use common portion are borne by the co-owners who have
the use of it. Major repairs and replacement follow the general rule and are
spread over **all** the fractions: para. 2 says the declaration *may* provide
otherwise, so absent a clause, the whole immovable pays. Redoing the
waterproofing of a private terrace is not for its beneficiaries alone. A module
with a single "restricted use" expense type is wrong by tens of thousands of
dollars. Each budget line therefore carries its nature, the restricted-use
portion, and a separate box for a declaration that provides otherwise, with
the clause.

**The general meeting does not adopt the budget.** Article 1072 CCQ: the board
of directors fixes the contribution *after consulting the general meeting*. The
consultation is a prerequisite, not a vote, and the module refuses to fix a
budget that has not been through it. The notice is transmitted *without delay*,
with no number of days in the text, so the module flags a notice that is
pending and invents no deadline. A special contribution has its own
consultation (art. 1072.1 CCQ), distinct from that of the annual budget.

**The contingency fund floor has three possible bases, and the module does not
pick one.**

| Floor | Base | Who | Source |
|---|---|---|---|
| 0.5 % | reconstruction value of the immovable | the **promoter**, until they obtain the study | art. 1071 para. 4 CCQ |
| 5 % | contributions to common expenses | a syndicate under the transitional regime, until the sums are fixed after its first study | Bill 16 (S.Q. 2019, c. 28), s. 153 para. 2 |
| none | the study's recommendations | any syndicate once the study is obtained | art. 1071 para. 3 CCQ |

The 5 % floor is widely described as abrogated. It left the Code, but it is in
force in the transitional sections of the statute.

Which of the two floors applies to a given syndicate before its study is a
reading of the texts, and so is the base of the 5 % (the whole contribution,
both funds included since art. 1072 CCQ places them in it, or the operating
budget alone, as was common practice before 2019). Those readings belong to the
notary or lawyer on file, on the declaration of co-ownership. The syndicate
therefore records **its own** floor and **its own** base in two fields. Until
it does, the module carries no figure and says so, citing both texts. What it
still does, because it is a comparison and not an interpretation: state the
date of the art. 1104 CCQ meeting it holds and whether that date precedes the
coming into force of the regulation by more than 30 days, the written
criterion of s. 151 of the same statute.

Once a figure is carried, the budget shows the gap between the sum budgeted to
the contingency fund and that reference.

## Contingency fund catch-up (Bill 16, ss. 153 and 154)

- When the study reveals the fund to be insufficient, the annual catch-up
  payment is computed so that the fund is sufficient within ten years of the
  **first** study, over the years that **remain**, never over ten. A renewed
  study does not reset that counter.
- The board must fix the sums no later than 30 days after the **first** annual
  meeting held since that study. The module finds that meeting and shows the
  deadline and its state (pending, overdue, met).
- The module never assesses whether the fund is sufficient: the shortfall is
  entered from the study, signed by the professional who wrote it.
- The catch-up payment, the fixing state and the five-year staleness of the
  reconstruction value (art. 1073 CCQ) change with the calendar, not with an
  entry. A daily scheduled action recomputes them, so that the figures on the
  syndicate always match the rule printed beside them.

## Self-insurance fund (art. 1071.1 CCQ and CQLR, c. CCQ, r. 4.1, s. 2)

The minimum annual contribution is computed from the highest deductible F and
the fund's capitalisation C: C at most F/2 gives F/2; between the two, the
difference; C at least equal to F gives nothing. Earthquake and flood
deductibles are excluded by the regulation; the module does not read insurance
policies, so the field is labelled accordingly. The reduction above $100,000 is
an **option** of the syndicate, not a rule: it applies only when its box is
ticked.

## Collection is not a matter of preference

The imputation of a payment is set by the Code, not by the manager. Article
1569 CCQ: the debtor states which debt they are paying, and may not pay in
advance over a debt already due without the creditor's consent, which is
recorded. Article 1570: interest before capital. Article 1571: an accepted
imputation is not redone. Article 1572, **and only where the debtor gave no
indication**: debts due first, oldest first, and proportionally between those
due on the same day, to the cent.

⚠️ The second paragraph of art. 1572, "the one the debtor has most interest in
paying", is **not applied and will not be**. That is a judgement, not a
calculation. The module says so on screen and lets the user impute by hand.

**Interest runs from default, never from the due date** (art. 1617, with arts.
1594 and 1595 CCQ): either the declaration stipulates that the mere lapse of
time puts the debtor in default, or a written extrajudicial demand is needed.
No rate is hard-coded: the legal rate does not come from the Civil Code, so the
syndicate enters its own, with a default of no interest at all. Interest is
computed period by period on the capital then outstanding, payments reducing it
at their date, not on today's balance over the whole span.

The charge follows the **fraction**, not the person (art. 1069 CCQ: the acquirer
is bound for the charges due on the fraction at the time of acquisition). A
fund call once issued is not rewritten when the fraction changes hands.

## Allocation to the cent

Largest-remainder method, basis by basis and never on the total, since the
bases differ. The shares add up exactly to the amount allocated, by
construction. Ties are broken on the lowest key so that a recomputation returns
what was already sent out. And four calls of 25 % do not add up to a financial
year that does not divide by four: the budget shows what remains to be called
rather than quietly spreading the cents.

## Deprivation of the right to vote

Article 1094 CCQ deprives the co-owner who, **for more than three months**, has
not paid their share of the common expenses. Three months exactly deprive
nobody, and the deprivation strikes the **person**, not the fraction: someone
holding three fractions and letting one lapse loses all their votes.

⚠️ Nothing ticks itself. The module computes the fact and proposes it on the
attendance lines of the general meeting; a button applies it, with the detail
of each arrear posted to the meeting's thread, because a payment received
yesterday and not yet entered is enough to make it fall.

Not to be confused with the 30 days of art. 2729 CCQ, which open the legal
hypothec. Recovery and the legal hypothec are outside this module.

## Statement of common expenses due (art. 1069 para. 2 CCQ)

🔴 This is the one deadline in the suite that runs **against** the syndicate:
past 15 days, the prospective acquirer is no longer bound and the claim must be
pursued against the seller, who has often gone. The statement therefore states
its own effect. The total includes interest (para. 1, "with interest"), the
adjustment to the last annual budget (para. 3) is attached and kept rather than
computed, and a statement once provided is not recomputed.

**The prior notice to the owner is actually sent.** Notice to the owner is the
condition of the syndicate's authorisation to provide the statement, not a
courtesy. The "notify the owner" action emails every owner on the register who
has an email address, naming the requester and the date of the request, and
posts the notice on the statement's thread. It is **refused when no owner is on
the register**: a notice with nobody to notify is no notice, and its date is
what unlocks the statement. Owners without an email address are named on the
thread as still to be notified by other means. Providing the statement is
refused until the notice is recorded and a budget is attached.

Not to be confused with the certificate of art. 1068.1 CCQ, requested by the
selling co-owner, nor with the documents of art. 1068.2 CCQ, requested by the
promising buyer (both in `bf_property_records`).

### The printed statement is the proof, not a summary of the record

Everywhere else in the suite a printed document restates what the record
already knows. Article 1069 para. 2 CCQ makes a claim depend on delivery within
fifteen days, so this one is different: it is what the prospective acquirer
will show their notary, and what the syndicate will show if it delivered in
time.

That is why the effect of the deadline sits at the top of the page, boxed, in a
different colour when the claim is lost. The notice given to the owner is on
the document too, with the names: a document that omits it proves only half of
what it claims to prove.

⚠️ **Nothing prescribes this document's form.** The certificate of art. 1068.1
CCQ has its content fixed by regulation; the statement of art. 1069 para. 2 has
none. The layout is therefore an editorial choice, and the footer says so
rather than letting the page pass for an official form.

⚠️ **A zero prints as a zero amount, never as a dash.** On a statement of
charges owed, "nothing is owed" and "we do not know" do not commit the
syndicate to the same thing, and a dash would say both at once.

## Budget against actual

The module keeps **no expenses**: no invoice, no supplier, no ledger, and no
dependency on `account`. "Budget against actual" therefore means the cycle of
art. 1072 CCQ: fixed, called, collected, still to call, line by line, with a
printable document for the general meeting that says so in as many words.
Collected means **capital**, since interest funds no line item, and it is
spread over the line items in proportion to what was called, because a payment
is imputed to a fraction's contribution, not to a budget line.

Article 1087 CCQ requires six documents with the notice of the annual general
meeting. The module produces one of them (the budget) and half of another (the
receivables side of the statement of debts and claims), and the list says for
each whether it produces it, so that a board does not turn up without a balance
sheet. It blocks no convocation.

## Language of the rules

The sentences that explain a computed rule (contingency basis, self-insurance,
interest, allocation, statement effect) are rendered when read, in the
reader's language, never stored: they pass as they are into the budget handed
to the general meeting and the statement handed to the prospective acquirer.

## Security

- Property users read budgets, fund calls, payments and statements; property
  managers create and edit them.
- Every decision (notice to the owner, providing a statement) is refused to
  anyone who is not the syndicate's side (the property manager role), before
  any email leaves.
- All four models are partitioned by company. The statement names the
  prospective acquirer, the owner and the owner's debts: that partition is not
  optional.
- Outgoing notices use the branded mail layout of the Symbifox branding module
  when it is installed, and Odoo's light layout otherwise.

## Dependencies

`bf_property_core`, `bf_property_governance` (the budget and the special
contribution point to the general meeting consulted). Deliberately **not**
`account`.

## Tests

The suite covers the three regimes of art. 1064, allocation to the cent, the
contingency floors and the ten-year catch-up, the self-insurance formula,
imputation under arts. 1569 to 1572, interest from default, arrears and
deprivation, the statement of charges due and its prior notice (sent, refused
without an owner, owners without email named), budget against actual, and the
reader's language of every rule, and the daily refresh of the contingency
fund's dates (154 tests).

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
