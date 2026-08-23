# Co-ownership — Maintenance log, contingency study and attestation (`bf_property_loi16`)

The documentary obligations that the 2019 reform put on a Quebec *syndicat de
copropriété divise*: the maintenance log, the contingency fund study, the
attestation to a buyer, the documents owed to a promissory buyer, and the
transitional calendar that dates all of them.

## What it does

| Model | Purpose |
|---|---|
| `bf.property.maintenance.log` | The maintenance log, its author, its independence conditions and its review interval |
| `bf.property.maintenance.item` | One item of the inventory: element, installation date, estimated remaining life, planned works |
| `bf.property.contingency.study` | The contingency fund study, its author and its recommendations |
| `bf.property.attestation` | The syndicat's attestation to a buyer, with its printable document |
| `bf.property.disclosure` | The documents owed to a promissory buyer |

## Two statutes, not one

The shorthand "the obligations of Loi 16" is wrong for part of the corpus. The
co-owner's liability insurance (art. 1064.1) and the self-insurance fund
(art. 1071.1) come from the 2018 insurance statute, not from the 2019 one. The
attribution changes the dates of coming into force, therefore the deadlines.

## The thresholds are in the regulations, not in the Code

Articles 1068.1, 1070.2 al. 2, 1071 al. 2, 1064.1 and 1073 delegate everything.
Reading the Code alone tells you no figure at all.

**Who may establish the log** (three cumulative conditions): membership of one
of four professional orders, namely engineers, **chartered appraisers**,
architects or professional technologists; professional activity concerning
principally the management, construction, renovation, appraisal or inspection
of immovables; and independence, meaning not a director, manager, co-owner or
occupant of the immovable, nor the spouse of one, nor an officer or employee of
a legal person that is. The commonly repeated list omits the appraisers, and
omits the independence conditions, which are the real operational constraint:
the manager of the immovable cannot establish the log.

**Who may carry out the study**: the same, plus an independent professional
accountant.

**Review interval**: five years by default. Ten years only if one of three
conditions of size is met, and the count of eight private portions **excludes**
storage spaces and parking spaces.

⚠️ **The study depends on the log.** They are sequential, not parallel: the
module refuses to record a study while the log is not established, and it names
what is missing rather than failing silently.

## Three regimes of documents to a buyer, never to be confused

| Article | Who asks | Deadline | Notice |
|---|---|---|---|
| 1068.1 | the seller | 15 days | none |
| 1069 al. 2 | a person proposing to acquire | 15 days | prior notice to the owner, **before** |
| 1068.2 | a promissory buyer | "with diligence" | notice to the owner **after**, on the exact content |

The second lives in the finance module, since it is the output of the arrears
register. ⚠️ Article 1068.2 sets **no deadline**: the module counts the days but
declares no lateness, and a test checks that it has no deadline field where the
other two have one.

⚠️ Article 1068.2 carries its own privacy reservation: the authorisation does
not cover the personal information of other co-owners. A review is required
before the documents are handed over, and any redaction is recorded.

## The attestation document

A printable report, letter format, **carrying no publisher's branding**: it is
the syndicat that attests and signs, and a notary files it. The eight points
appear in the order of the regulation, with the three windows of three, five
and ten years recalled in the labels. An attestation that has not been handed
over prints as a draft, and it is dated from the handover, not from the
printing. A nil amount prints as a zero and not as a dash, because "nothing" is
not "we do not know".

The attestation does not exist before the promoter's handover meeting
(art. 1068.1 al. 3): the module refuses to create one rather than presuming a
date.

## The transitional calendar

Anchor point: the regulation came into force on **14 August 2025**. That is
derived from the text, not from commentary: the decree is dated 16 July 2025,
published in the *Gazette officielle du Québec* of 30 July 2025, and its
article 15, omitted from the consolidated text, provides that it comes into
force on the fifteenth day following publication.

Four regimes follow, according to the date of the meeting held under art. 1104:
three years for existing syndicats, six months at the promoter's charge around
the pivot date, thirty days thereafter, and in the meantime the 5 % floor on
the contingency fund. Sixty days to make the log and the study available.

⚠️ Where a wording admits two readings, the module takes the **earlier**
deadline. A deadline shown a day too early costs nothing; a day too late would
miss a forfeiture.

⚠️ **Without an attached building the interval stays at five years**, not ten:
the module does not presume a derogation it cannot verify.

## Dependencies

`bf_property_core`, `bf_property_finance`.

## Tested

67 tests on staging: the author and independence conditions, the log to study
sequence, the review intervals and the private-portion count, the three
document regimes and their deadlines, the attestation and its refusals, and the
four transitional regimes.

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
