# Co-ownership: maintenance log, contingency study and certificate (`bf_property_records`)

The documentary obligations that the 2019 reform put on a Quebec syndicate of
divided co-ownership: the maintenance log, the contingency fund study, the
certificate to a buyer, the documents owed to a promising buyer, and the
transitional calendar that dates all of them.

## What it does

| Model | Purpose |
|---|---|
| `bf.property.maintenance.log` | The maintenance log, its author, its independence conditions, the on-site examination declaration and its review interval |
| `bf.property.maintenance.item` | One item of the inventory: where it is, what it is, installation date, maintenance, contracts, inspections, manufacturer's manual, condition, remaining life, planned and completed works |
| `bf.property.contingency.study` | The contingency fund study, its author and its recommendations |
| `bf.property.attestation` | The syndicate's certificate to a buyer (art. 1068.1 CCQ), with its printable document |
| `bf.property.disclosure` | The documents owed to a promising buyer (art. 1068.2 CCQ), and their transmission to the owner |

It also extends the syndicate with the transitional calendar, and the building
with the count of private portions that decides the review interval.

Four scheduled actions run daily, because these deadlines arise from the
calendar, not from an entry: the log's review and annual update, the
five-yearly renewal of the study, the 15 days of the certificate, and the
syndicate's calendar of legal deadlines.

## Two statutes, not one

The shorthand "the obligations of Bill 16" is wrong for part of the corpus. The
co-owner's liability insurance (art. 1064.1 CCQ) and the self-insurance fund
(art. 1071.1 CCQ) come from the 2018 insurance statute, not from the 2019 one.
The attribution changes the dates of coming into force, therefore the
deadlines.

## The thresholds are in the regulations, not in the Code

Articles 1068.1, 1070.2 para. 2, 1071 para. 2, 1064.1 and 1073 CCQ delegate
everything. Reading the Code alone tells you no figure at all.

**Who may establish the log** (three cumulative conditions): membership of one
of four professional orders, namely engineers, **chartered appraisers**,
architects or professional technologists; professional activity concerning
principally the management, construction, renovation, appraisal or inspection
of immovables; and independence, meaning not a director, manager, co-owner or
occupant of the immovable, nor the spouse of one, nor a shareholder, officer,
director or employee of a legal person, partnership or trust that is a
co-owner, occupant or manager of it. The commonly repeated list omits the
appraisers, and omits the independence conditions, which are the real
operational constraint: the manager of the immovable cannot establish the log.

**Who may carry out the study**: the same, plus an independent professional
accountant.

**Review interval**: five years by default. Ten years only if one of three
conditions of size is met, and the count of eight private portions **excludes**
accessory portions such as storage spaces and parking spaces.

⚠️ **The study depends on the log.** They are sequential, not parallel: the
module refuses to record a study as obtained while its log is not established,
and it lists what is missing rather than failing silently. Once obtained, the
study feeds the contingency fund data of `bf_property_finance` (study date,
recommended amount, shortfall, and the date of the **first** study, which a
renewed study never resets).

## Where an item is

When a syndicate groups several buildings, an inventory that does not say
where each item is sends nobody anywhere. Each item carries its building and,
where relevant, its fraction. The building is deduced from the common portion
or the fraction when it is left empty, including on imports that call no
onchange; a building given explicitly that contradicts them is refused rather
than silently replaced. Naming a fraction is refused unless the item is
declared to be in a private portion, and a private-portion item enters the log
only if the syndicate is answerable for its maintenance.

## The on-site examination declaration

The regulation (CQLR, c. CCQ, r. 8.01, s. 6) requires the person who establishes
or revises the log to sign a dated declaration that the common portions and the
items were examined on site by them or under their supervision. It is the one
document in the suite whose text expressly requires a **signature**.

The module prints that declaration from the log, as a draft until it is dated.
When the signature module of the suite is installed, the log's author can sign
it electronically without an account in the instance. ⚠️ The declaration box
remains editable by hand, deliberately: a paper declaration signed and filed in
the log is perfectly valid, and the module offers one more path without closing
any.

## Three regimes of documents to a buyer, never to be confused

| Article | Who asks | Deadline | Notice |
|---|---|---|---|
| 1068.1 CCQ | the selling co-owner | 15 days | none |
| 1069 para. 2 CCQ | a person proposing to acquire | 15 days | prior notice to the owner, **before** |
| 1068.2 CCQ | a promising buyer | "with diligence" | transmission to the owner **after**, on the exact content |

The second lives in `bf_property_finance`, since it is the output of the
arrears register. ⚠️ Article 1068.2 sets **no deadline**: the module counts the
days but declares no lateness, and a test checks that it invents none.

⚠️ Article 1068.2 carries its own privacy reservation: the authorisation does
not cover the personal information of other co-owners. Providing the documents
is refused until the privacy review is recorded, and until the documents
provided are listed, since the owner must later be told exactly what they were.

**The transmission to the owner is actually sent** (art. 1068.2 para. 2 CCQ).
The "transmit to the owner" action emails every owner on the register who has
an email address the exact list of what was provided to the promising buyer,
and when, and records it on the request's thread. It is **refused when no owner
is on the register**, since the transmission would go to nobody. Owners without
an email address are named on the thread as still to be notified by other
means. A transmission cannot predate the handover of the documents.

## The certificate document

A printable report, letter format, **carrying no publisher's branding**: it is
the syndicate that certifies and signs, and a notary files it. The eight points
appear in the order of the regulation, with the three windows of three, five
and ten years recalled in the labels. A certificate that has not been handed
over prints as a draft, and it is dated from the handover, not from the
printing. A nil amount prints as a zero and not as a dash, because "nothing" is
not "we do not know".

The certificate does not exist before the promoter's handover meeting
(art. 1068.1 para. 3 CCQ): the module refuses to create one rather than
presuming a date.

## The transitional calendar

Anchor point: the regulation came into force on **14 August 2025**. That is
derived from the text, not from commentary: the decree was published in the
*Gazette officielle du Québec* of 30 July 2025, and its section 15, omitted from
the consolidated text, provides that it comes into force on the fifteenth day
following publication.

The regimes follow the date of the meeting held under art. 1104 CCQ: three
years for existing syndicates, six months at the promoter's charge around the
pivot date, thirty days thereafter, and sixty days to make the log and the
study available.

⚠️ Where a wording admits two readings, the module takes the **earlier**
deadline. A deadline shown a day too early costs nothing; a day too late would
miss a forfeiture.

⚠️ **Without an attached building the interval stays at five years**, not ten:
the module does not presume a derogation it cannot verify.

## Security

- Property users read logs, items, studies, certificates and requests;
  property managers create and edit them.
- Providing documents and transmitting them to the owner are refused to anyone
  who is not the syndicate's side (the property manager role), before any
  email leaves.
- All records are partitioned by company. The certificate names the selling
  co-owner; the request names the promising buyer and the owner, and its
  privacy note says what was redacted: that partition is not optional.
- Outgoing emails use the branded mail layout of the Symbifox branding module
  when it is installed, and Odoo's light layout otherwise.

## Dependencies

`bf_property_core`, `bf_property_finance` (the certificate carries the
contributions, the budget and the self-insurance fund; the study feeds the
contingency fund data, which live there).

## Tests

The suite covers the author and independence conditions, the log to study
sequence, item location, the review intervals and the private-portion count,
the three document regimes and their deadlines, the transmission to the owner
(sent, refused without an owner), the certificate and its refusals, the
transitional regimes, and the reader's language of computed sentences
(86 tests).

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
