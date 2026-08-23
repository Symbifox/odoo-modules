# Co-ownership — General meetings (`bf_property_governance`)

Convocation, quorum, weighted votes and majorities for a Quebec *syndicat de
copropriété divise*, encoded from the Civil Code rather than parameterised.

## Why it exists

Article 1101 C.c.Q. deems unwritten any clause of the declaration that alters
the number of votes required. A configurable percentage is therefore not a
feature, it is a defect. This module encodes the statutory thresholds and shows
the article next to the result.

The stakes are set by article 1103: an error in the counting of votes is a
ground to annul a decision of the meeting, and the action must be brought
within 90 days on pain of forfeiture. Vote arithmetic here is not a
convenience, it is the product.

## What it does

| Model | Purpose |
|---|---|
| `bf.property.assembly` | A meeting: convocation, agenda, quorum, totals, minutes |
| `bf.property.assembly.attendance` | One line per co-owner and fraction: presence, base votes, votes retained |
| `bf.property.resolution` | A resolution, its majority rule and its result |
| `bf.property.secret.ballot` | Register, urn and receipt for a secret ballot |

Wizard included to open and count a secret ballot.

## The vote calculation, in the order the Code imposes

Each rule measures itself on the result of the previous one, so the order is
not decorative.

1. Base votes, proportional to the fraction's quote-part and split between
   undivided co-owners according to their shares (art. 1090 al. 1).
2. A fraction held by the syndicat itself carries no vote, and the total of the
   votes that may be expressed is reduced accordingly (art. 1076). This is read
   from the register, not from the attendance sheet: an attendance sheet that
   was never loaded would otherwise hand those votes back to the total.
3. Deprivation of the right to vote (art. 1094) and any reduction entered by
   hand. Both come out of the syndicat's total as well (art. 1099).
4. The presumed mandate between undivided co-owners: an absent co-owner passes
   their votes to the others, pro rata (art. 1090 al. 2).
5. Cap for co-ownerships of fewer than five fractions (art. 1091), measured on
   the sum of the votes of the other co-owners present or represented, hence
   after step 4.
6. Cap on the promoter's votes (art. 1092).

Absence is not a reduction: an absent co-owner keeps their votes, they simply
do not express them.

## Majorities

- **Art. 1096** — majority of the votes of the co-owners present or
  represented, including votes to amend the by-laws of the immovable or to
  correct a clerical error in the declaration.
- **Art. 1097** — three quarters of the votes of the co-owners present or
  represented, for five enumerated matters. ⚠️ **No condition in number since
  10 January 2020**, contrary to what most online commentary still says.
- **Art. 1098** — three quarters of the co-owners representing 90 % of the
  votes of all the co-owners. Unchanged since 1991, and the only majority in
  number that survives.

## Secret ballot, and what a weighted vote cannot hold

The right to require a secret ballot comes from art. 351 al. 2 C.c.Q., which
reaches the syndicat through art. 1039 (it is a legal person) and art. 334.
Article 1089.1 presupposes that right instead of creating it: it only sets the
conditions under which a participant attending remotely may vote *when such a
vote is requested*. Coding secrecy for remote meetings alone would be a
mistake of source.

⚠️ **Weight betrays.** A weighted ballot has to carry the number of votes,
because the count needs it. A unique quote-part in the immovable, which is the
ordinary case, turns that number into the signature of its author for whoever
holds the register. Secrecy only holds between ballots of equal weight, so the
module counts the ballots that their weight isolates and displays that count
rather than promising a secrecy the arithmetic cannot keep.

Three details make the design hold: ballots are created in one go and shuffled
before any is cast; the urn carries `_log_access = False`, because write
timestamps would reconstruct the order of passage and therefore the voters; and
each voter gets an anonymous key, without which the urn would count ballots
where art. 1098 counts co-owners. A secret ballot does not reopen: admitting a
latecomer would require keeping the person-to-ballot link that the whole model
exists not to keep.

## Meetings held by technological means

Article 1088.1 allows them without prior agreement, unlike the general rule of
art. 344. Nothing in that article requires announcing the means in the notice;
what does is art. 346, which requires the notice to state the place where the
meeting is held. For a meeting with no room, the connection link is the place.

## Dependencies

`bf_property_core`.

## Tested

110 tests on staging: the six steps of the vote calculation and their order,
the three majorities, quorum and reconvened meeting, the syndicat-held
fraction, the presumed mandate, the caps, the secret ballot including the
exposure count, and the convocation window.

⚠️ A counter-intuitive result is not a bug. Under arts. 1091 and 1099, in a
co-ownership of fewer than five fractions, the majority holder alone with one
other co-owner never reaches the quorum of art. 1089 al. 1. That is what the
law says, and a separate test guards each of the two cases so that nobody
"fixes" the first.

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
