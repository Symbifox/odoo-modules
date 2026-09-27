# Co-ownership: general meetings and board of directors (`bf_property_governance`)

Convocation, quorum, weighted votes and majorities for a Quebec syndicate of
divided co-ownership, encoded from the Civil Code rather than parameterised,
and the meetings of the board of directors.

## Why it exists

Article 1101 CCQ deems unwritten any clause of the declaration that alters the
number of votes required. A configurable percentage is therefore not a
feature, it is a defect. This module encodes the statutory thresholds and shows
the article next to the result.

The stakes are set by article 1103 CCQ: an error in the counting of votes is a
ground to annul a decision of the general meeting, and the action must be
brought within 90 days on pain of forfeiture. Vote arithmetic here is not a
convenience, it is the product.

## What it does

| Model | Purpose |
|---|---|
| `bf.property.assembly` | A general meeting: convocation, agenda, quorum, totals, minutes |
| `bf.property.assembly.attendance` | One line per co-owner and fraction: presence, participation mode, base votes, votes retained |
| `bf.property.resolution` | A resolution, its majority rule and its result |
| `bf.property.vote` | A vote expressed on a resolution |
| `bf.property.secret.ballot` | Register, urn and receipt for a secret ballot |
| `bf.property.council.meeting` | A meeting of the board of directors and its minutes |

Three wizards issue the receipts of a secret ballot, deposit a ballot, and
verify a receipt. A printable minutes document serves both the general meeting
and the board.

Two scheduled actions run daily, because these states depend on today's date,
not on an entry: the transmission state of general meeting minutes, and the
state and minutes transmission of board meetings.

## Convocation

Article 346 CCQ, which reaches the syndicate through arts. 1039 and 334: at
least 10 and at most 45 days before the meeting. The module states whether the
notice complies, is too early or too late, and distinguishes a meeting whose
notice has not yet gone out from a meeting **held without ever having been
convened**. A late notice is arguable; a meeting held with no notice at all is
another subject. That distinction is drawn from the meeting's state, not from
today's date. The module blocks nothing: what a missing notice entails is for a
court to say.

## The vote calculation, in the order the Code imposes

Each rule measures itself on the result of the previous one, so the order is
not decorative.

1. Base votes, proportional to the fraction's relative value and split between
   undivided co-owners according to their shares (art. 1090 para. 1 CCQ).
2. A fraction held by the syndicate itself carries no vote, and the total of
   the votes that may be expressed is reduced accordingly (art. 1076 CCQ). This
   is read from the register, not from the attendance sheet: an attendance
   sheet that was never loaded would otherwise hand those votes back to the
   total.
3. Deprivation of the right to vote (art. 1094 CCQ) and any reduction entered
   by hand. Both come out of the syndicate's total as well (art. 1099 CCQ).
4. The presumed mandate between undivided co-owners: an absent co-owner passes
   their votes to the others, pro rata (art. 1090 para. 2 CCQ).
5. Cap for co-ownerships of fewer than five fractions (art. 1091 CCQ),
   measured on the sum of the votes of the other co-owners present or
   represented, hence after step 4.
6. Cap on the promoter's votes (art. 1092 CCQ).

Absence is not a reduction: an absent co-owner keeps their votes, they simply
do not express them.

## Majorities

- **Art. 1096 CCQ**: majority of the votes of the co-owners present or
  represented, including votes to amend the by-laws of the immovable or to
  correct a clerical error in the declaration.
- **Art. 1097 CCQ**: three quarters of the votes of the co-owners present or
  represented, for five enumerated matters. ⚠️ **No condition in number since
  10 January 2020**, contrary to what most online commentary still says.
- **Art. 1098 CCQ**: three quarters of the co-owners representing 90 % of the
  votes of all the co-owners. Unchanged since 1991, and the only majority in
  number that survives.

## Secret ballot, and what a weighted vote cannot hold

The right to require a secret ballot comes from art. 351 para. 2 CCQ, which
reaches the syndicate through art. 1039 (it is a legal person) and art. 334.
Article 1089.1 presupposes that right instead of creating it: it only sets the
conditions under which a participant attending remotely may vote *when such a
vote is requested*. Coding secrecy for remote meetings alone would be a
mistake of source.

⚠️ **Weight betrays.** A weighted ballot has to carry the number of votes,
because the count needs it. A unique relative value in the immovable, which is
the ordinary case, turns that number into the signature of its author for
whoever holds the register. Secrecy only holds between ballots of equal
weight, so the module counts the ballots that their weight isolates and
displays that count rather than promising a secrecy the arithmetic cannot
keep.

Three details make the design hold: ballots are created in one go and shuffled
before any is cast; the urn carries `_log_access = False`, because write
timestamps would reconstruct the order of passage and therefore the voters; and
each voter gets an anonymous key, without which the urn would count ballots
where art. 1098 counts co-owners. A secret ballot does not reopen: admitting a
latecomer would require keeping the person-to-ballot link that the whole model
exists not to keep. The urn must be an exact permutation of the register, and
the check says so when it is not.

### "Transient" does not mean "not in the database"

The urn carries no name, the register carries no choice, and only the SHA-256
of each receipt is stored. The wizards around it are where the link could leak:
a transient record is a row in an ordinary table that Odoo's vacuum collects
later, and only when another record of the same model is created. It is a
safety net, never the measure. Three rules follow:

1. **The receipt list is erased when the officer has finished handing out
   receipts.** The button says what it does and deletes the row. The model's
   lifetime is thirty minutes for an abandoned window, and opening a ballot
   first sweeps whatever a previous session left behind.
2. **The deposited code is cleared inside the deposit's own transaction.** The
   code and the choice together are exactly the link the urn exists not to
   keep.
3. **The verified code is cleared after the check**, for the same reason.

What remains true: between the moment the officer sees the list and the moment
they click, the link exists in the database. It is unavoidable, since handing
the right receipt to the right co-owner means knowing which is whose. It is
counted in minutes of an open window rather than in hours, and the on-screen
warning says so instead of promising a secrecy the table does not keep.

The deposit method carries its own state checks: it is public, so conditions
posted only in the wizard would bound only the wizard, and a ballot could enter
the urn after the meeting had closed.

## Meetings held by technological means

Article 1088.1 CCQ allows them without prior agreement, unlike the general rule
of art. 344. Nothing in that article requires announcing the means in the
notice; what does is art. 346, which requires the notice to state the place
where the meeting is held. For a meeting with no room, the connection link is
the place. Each attendance line records whether the co-owner took part in
person or remotely, and the meeting's announced mode and the lines must agree:
a remote participation cannot be entered on a meeting announced in person, nor
the reverse.

## The board of directors (arts. 1084.1 and 1086.1 CCQ)

The board fixes the contribution after consulting the general meeting
(art. 1072), consults before any special contribution (art. 1072.1), has the
maintenance log drawn up and kept current (art. 1070.2), obtains the
contingency fund study and fixes the sums to be paid into it (Bill 16,
S.Q. 2019, c. 28, s. 153 para. 1), decides how the fund is used (art. 1071),
and designates the person before whom the register is consulted (art. 1070.1).
Its meetings are recorded here.

🔴 **Article 1070 para. 1 CCQ puts the board's minutes in the register**, on
the same footing as the general meeting's. Without this object, half of the
register would be missing.

⚠️ **No prior agreement to ask for.** Article 1084.1 does for board meetings
what 1088.1 does for the general meeting, and it deliberately departs from the
general regime for legal persons: article 344 requires the directors to be "all
in agreement" before sitting by technological means; the co-ownership rule does
not. A "the directors consented" checkbox would be an invented condition, and a
test asserts that no such field exists.

⚠️ **The condition is immediate communication among ALL participants.** A
one-way broadcast, where you listen without being able to speak, does not
satisfy the article. The module cannot observe that; the checkbox is an
attestation by whoever chairs, and it stays in the file.

⚠️ **Thirty days, and the clock does not run on a cancelled meeting.** Article
1086.1 gives the board the same delay that article 1102.1 gives the general
meeting. The constant is named separately in each model, so that the day one of
the two changes, the other does not follow by accident.

### What this object deliberately does not hold

- **The board's composition and the election of its members.** Nothing in the
  sourced corpus fixes the number of directors, the length of their terms or
  how they are elected: the declaration of co-ownership does, and it varies.
  Modelling directors would mean choosing in its place.
- **The participants, one by one.** They are named in the minutes. Putting them
  in a structured field would be the first step toward the roster the previous
  point rules out.
- **Written resolutions outside a meeting.** Article 1102.1 names them for what
  the board transmits *following a general meeting*. Whether the board may
  itself decide by written resolution without meeting needs a source the rule
  book does not yet have, and until it does the module does not invent the
  regime.

## Minutes

Articles 1102.1 and 1086.1 CCQ require the minutes of the general meeting and
of the board to be **transmitted** within 30 days; neither requires a
signature. The module records the transmission date and states whether it was
within the delay; it does not send the minutes itself, and it refuses to record
the transmission of a board meeting's minutes that have not been written.

The printed minutes **write nothing**: the text is the syndicate's, entered in
its field, and the document dresses, dates and identifies it. It prints as a
draft until the minutes are written and transmitted. The time of the meeting
prints in the company's time zone first, then the user's, so that two directors
in two time zones never print two contradictory minutes.

## Security

- Property users read meetings, attendance, resolutions, votes, ballots and
  board meetings, and can verify a secret-ballot receipt; property managers
  create and edit them, and alone can issue receipts and deposit ballots.
- Recording the transmission of board minutes is refused to anyone who is not
  the syndicate's side (the property manager role).
- General meetings and board meetings exist only for an organisation that is a
  syndicate of co-owners.

## Dependencies

`bf_property_core`.

## Tests

The suite covers the six steps of the vote calculation and their order, the
three majorities, quorum and reconvened meeting, the syndicate-held fraction,
the presumed mandate, the caps, the convocation window, remote participation
and the secret ballot (no path from person to choice in the schema, urn as an
exact permutation of the register, exposure count), board meetings, and the
reader's language of computed sentences (139 tests).

⚠️ A counter-intuitive result is not a bug. Under arts. 1091 and 1099 CCQ, in
a co-ownership of fewer than five fractions, the majority holder alone with one
other co-owner never reaches the quorum of art. 1089 para. 1. That is what the
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
