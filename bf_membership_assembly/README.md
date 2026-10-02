# Membership: general meetings and votes (`bf_membership_assembly`)

A members' meeting is rarely challenged on the substance. It is challenged on
a notice sent too late, a person who voted without being in good standing, one
proxy too many, a quorum counted from memory. This add-on keeps track of those
details, in the order they happen.

Nothing in Odoo Community holds a members' meeting: Odoo's `membership` module
knows a fee, not a voting right. The base `bf_membership` keeps the register;
this add-on builds on it and changes nothing in it.

The interface, the notice template and the minutes are in French; the labels
below are quoted as they appear on screen.

## How a meeting runs

1. **Create the assembly** (always as a draft): annual or special, date and
   time, place or connection link, **record date**, quorum rule, whether
   proxies are allowed, chair and secretary.
2. **Build the voter list** (« Bâtir la liste des votants ») as of the record
   date.
3. **Convene** (« Convoquer »): the notice date is today's, the notice period
   is checked against it, and Odoo's email composer opens pre-filled. The list of members to
   notify by post is ready. When no member has consented to email notices,
   the list of postal notices opens instead.
4. **Record attendance and proxies** at the door.
5. **Open** the assembly: the voter list freezes and the quorum is logged.
6. **Vote on proposals**, by show of hands or by secret ballot.
7. **Close** it: proposals, attendance, chair and secretary freeze. The
   minutes stay editable and print as a PDF: through the button once the
   assembly is open, and through the Print menu at any time, from draft on.

## Models

| Model | What it holds |
|---|---|
| `bf.membership.assembly` | The assembly: kind, date, place or link, notice periods, notice date, record date, quorum, proxies, state, chair and secretary, minutes, attached documents |
| `bf.membership.assembly.voter` | One line per voting member: the membership that gives the vote, the person who exercises it, the notice channel and the date the email notice was sent, attendance, proxy holder |
| `bf.membership.assembly.proposal` | A proposal: mover, seconder, required majority, voting method, totals, result |
| `bf.membership.assembly.ballot` | The register of ballots handed out in a secret ballot. **It holds no choice** |

## Eight decisions, and why

### 1. Voters are read at the record date, never today

The base module's member status is computed for today and changes every
night. A list built on it would change between the notice and the meeting
without anyone deciding so.

The list takes the memberships that were **accepted and settled** (paid or
exempt) and whose period covers the record date, in a category that votes. It
deliberately does not use the base module's "in good standing today" test: a
membership that covered the record date and has expired since still counts.
Two choices follow:

* **The payment date is not read.** The register does not always know when
  the money arrived; excluding a member because a cheque was recorded late
  would take away their vote over a data-entry delay.
* **A withdrawal dated on or before the day of the meeting removes the person
  from the list**, even if they were a member at the record date: they are no
  longer a member when the vote takes place. The list is read when it is
  built: a withdrawal recorded after « Bâtir la liste des votants » does not
  remove the person by itself. The team rebuilds the list, which the button
  allows as long as the assembly is convened, and the rebuilt list drops the
  line. Once the assembly is open the list is frozen: the chair records the
  withdrawal in the minutes. A rebuilt list names, in the thread, the members
  it removes and, after the convocation, those it adds.

Members in their grace period (expired, within the category's grace period)
do not vote by default; « Les membres en grâce votent » admits them when the
by-laws say so.

### 2. A member organisation: one line, one vote

The vote belongs to the member; the person who exercises it is the
organisation's voting delegate in office. One line per delegate would give the
organisation as many votes as it names people, so an organisation has exactly
one line, and **one vote carried by one designated delegate**. The delegate is
chosen among those in office at the record date or on the day of the meeting:
a replacement between the two does not cost the organisation its vote.

An organisation **without a voting delegate** keeps its line, because it is
entitled to the notice. The line says so, the assembly shows the count, and
nobody can record it as present, or give a proxy on its behalf, until a
delegate is named: nobody has authority to sign for it. A member who is a
person votes in person; to be represented, they give a proxy.

Every change of attendance, proxy holder or person voting is logged in the
assembly's thread, with the before and after values, under the name of the
person who made it: that is what makes the quorum and the votes.

The list is built from the register, and the interface offers no way to add
or remove a line by hand: the view forbids it. A line can still be **added or
removed directly**, through an RPC call or an import; an added line must tell
the truth: it
cites the member's own membership, and the member must be eligible at the
record date, by the same test as the built list. Its grace flag and notice
channel are computed exactly as the built list would compute them; they cannot
be typed, nor slipped in through a context default. Each line added or
removed by hand is logged in the assembly's thread under the name of the
person who did it: a convened list that loses a line without a trace would
lose a convened member without anyone knowing. A line never changes member:
it is removed, and the right member's line added.

### 3. Convening never sends anything

« Convoquer » checks the notice period, sets the notice date, builds the list
and **opens the composer** pre-filled: recipients, subject, text and attached
documents. A person reads it over and clicks Send. A notice sent by mistake to
eight hundred members cannot be recalled. When no member has consented to email
notices, « Convoquer » opens the list of postal notices instead.

The notice is rendered in the **organisation's language** (the language of
the company's contact), not in the language of the person who sends it: its
dates are spelled in that language. The template's text ships in French, and
uses its translation for that language when one exists. The composer is
there to review the notice; it posts nothing in the thread, so no member
convened becomes a follower of the assembly (otherwise they would then receive
by email every internal note written on the record).

**The notice goes out member by member.** When it is sent, each member
receives their own email, addressed to them alone, with the notice layout, in
the organisation's language, with the attached documents, and without the
hidden "internal communication" preheader that mail clients show as a
preview. No message in the thread carries the list of recipients: a message
posted with its recipients could be read by each of them, including an
employee without the Members role who was convened, or a member with portal
access, who would then learn who else was convened. The assembly's thread
receives a count note (how many members, by whom, on what date) that names
nobody; for the Members role, the recipients are on the voter list (email
notices).

A member who has withdrawn their consent to email notices, or their address,
since the convocation does not receive the email: when the notice is sent,
their notice goes by post, and the thread says so.

**Proof of notice.** The composer can be reviewed, but sending never leaves
the list:

* the notice is sent only from a convened assembly, even by a call that
  supplies the notice context;
* it goes only to members whose line says « Courriel ». A recipient added to
  the composer who is not on such a line makes the sending refused;
* a member whose line says « Courriel » but who was removed from the
  recipients receives nothing by email: their line moves to « Poste », they
  join the list of postal notices, and the thread names them;
* each notified line carries the date and time its notice was sent (« Avis
  envoyé par courriel le »), set by the sending and never typed. The proof
  does not depend on the emails, which are deleted once sent;
* resending to a single person leaves the other lines already notified
  untouched: they keep their channel and date, and the thread records the
  resend without naming them;
* resetting to draft clears the sending dates of the previous notice (the
  thread records it): the next convocation goes out to everyone again;
* the notice is never sent through a mass mailing, which would escape these
  checks;
* the notice template leaves by no other path, outside superuser: not through
  a composer opened without the « Avis par courriel » button (mass mailing or
  ordinary message), not through `send_mail`, not through the thread
  (`message_post_with_source`, `message_mail_with_source`).

### 4. Email with consent, post for everyone else, fixed by the convocation

A member receives the notice by email **if they consented and have an
address** (the base module's « Accepte les avis par courriel »); otherwise by
post. For a member organisation it is the organisation that is convened: its
consent and email count, not its delegate's.

The channel of a line is **fixed by the convocation**: it says how the notice
was given. Rebuilding the list after convening does not rewrite it (only a new
line receives its channel), and it is never typed by hand. The list of
postal notices, with addresses, opens from the assembly (« Avis par la
poste ») and can be exported.

### 5. Proxies: refused by default, capped, never chained

Many by-laws exclude them, so « Procurations permises » is unticked by
default. Once allowed:

* the proxy holder is a voter who is **present**, on site or remotely;
* nobody gives a proxy to themselves;
* **no chains, in either direction**: a proxy cannot go to someone who gave
  their own, and someone who holds proxies can neither give theirs nor leave
  without handing them back. Otherwise a vote would move without a written
  instrument;
* a cap per holder (one by default; zero means no cap), counted per person who
  holds the proxies, over all their lines: for themselves, and as the person
  voting for an organisation. It is counted again when the person voting on a
  line changes (the line brings along the proxies it holds), and again at
  opening;
* an organisation without a voting delegate gives no proxy.

The quorum counts the voters who are present and those represented by a
proxy holder who is present.

### 6. Abstentions do not count, a tie is rejected

The majority is measured on the votes **cast**, for and against. Counting
abstentions would defeat proposals the assembly actually adopted.

* Simple majority: **more** than half. A tie is rejected.
* Two thirds: **at least** two thirds. Four votes out of six pass.
* Percentage: at least the percentage set, which must be above 50 %.

Two thirds are compared in integers (3 × for ≥ 2 × cast): a floating-point
division would fail them at the exact bound. A percentage is compared with
`float_compare` to six decimals (100 × for against cast × percentage), which
absorbs floating-point noise.

A guard refuses to count more votes than there are voices in the room (for a
show of hands) or more ballots than were handed out (for a secret ballot): it
is what catches a count entered from memory. It runs again at closing:
attendance removed after the count would otherwise make it lie. Results can
only be entered while the assembly is open.

**Once a result is entered, the rule no longer changes.** Changing the
required majority afterwards would make the proposal pass or fail on the same
totals; the totals themselves can still be corrected. The lock reads a flag,
« Dépouillement commencé » (count started), set at the first result entered
and never cleared, not the totals of the moment: in a show of hands the totals
can be reset to zero, and a lock read on them would lift. From that flag on,
the majority, its percentage and the voting method are frozen. Neither an
entry nor a context default can set or clear the flag.

What the assembly put to the vote freezes at the same moment: the title, the
text, the mover and the seconder. The bridge to the corporate register copies
the text as adopted; changed after the vote, it would register a resolution
the assembly never voted on. Before the vote they can still change (an
amendment).

A proposal has no thread of its own, so every change of its title, text,
mover or seconder, every entry or correction of the totals and every change
of the required majority is logged in the assembly's thread, with the before
and after values and the result, under the name of the person who made it.
Totals reset to zero and entered again therefore leave every count visible.

### 7. Secret ballot: a register without choices, a count without names

This is the separation of the paper ballot:

* the **register** (`bf.membership.assembly.ballot`, « Remettre les
  bulletins ») says that a voter was handed a ballot for a proposal, and to
  whom it was given (their proxy holder, if represented). One per voter and
  per proposal, proxies included. **It holds no choice**;
* the **count** holds only **totals**, entered by the scrutineers: for,
  against, abstentions, spoiled ballots;
* the guard that ties the two: **no more ballots are counted than were handed
  out**.

No line, no field and no record order links a person to what they put in the
box, because what they put in it never enters the database one ballot at a
time. A test checks this by introspection: one more field on the register
fails it.

The voting method no longer changes once ballots have been handed out. Once
a result has been entered, **no further ballot is handed out**, and the
ballots already handed out can no longer be taken back. A started count can be
corrected, but **not erased**. Otherwise a late arrival could receive a ballot
after a first count, and the difference between the two counts would reveal
how that person voted.

### 8. What freezes, and when

| From | What no longer changes | Why |
|---|---|---|
| Convening | Company, kind, date, notice periods, record date, admission of members in grace; the notice channel of each line; the content and binding of attached documents | Changing them means convening another meeting: reset to draft (the notice date is cleared) and convene again. The documents went out with the notice |
| Opening | The voter list and the person voting for each organisation; the quorum and proxy rules | The chair records the quorum and receives proxies under rules fixed in advance |
| The first count of a proposal, even if its totals are reset to zero afterwards | Its required majority and voting method; its title, text, mover and seconder | Changing the rule afterwards would make the proposal pass or fail on the same totals; changing the text would register what the assembly did not vote on |
| Closing | Attendance, proxies, proposals and their totals, the ballot register, the chair and the secretary | The minutes say what the assembly decided, and the chair and secretary certify them |

At every stage, a voter line or a proposal stays with its assembly: neither
can be moved to another one, so a proposal voted in one assembly can never
appear as adopted by another.

**Computed results cannot be written.** Every stored computed field (the
assembly's counts and quorum, a line's voice, a proposal's result, votes cast
and number of ballots) is refused on create and write outside superuser. The
ORM would otherwise accept the value and keep it until the next
recomputation: an RPC client could set "adopted" on a rejected proposal, or a
quorum reached with nobody in the room.

**The notice date is never typed.** Convening sets it to the actual day the
notice goes out, and the notice period is counted from it: a backdated notice
date would let a late notice pass the minimum period. It cannot be set at
creation, through a context default, or afterwards.

**A contact held by an assembly is neither deleted nor merged without the
Members role.** The base module holds the contacts that carry a membership or
a delegation; this module adds those on a voter line (as member or as the
person voting), those who received a ballot, and those who chair, keep the
minutes of, or scrutinise an assembly that is no longer a draft. Without the
role, deleting them archives them silently, and the archiving is noted in the
thread of each assembly concerned; merging them receives the base module's
neutral refusal. Otherwise the chair of a closed assembly would be emptied,
scrutineers would be removed in cascade, and Odoo's native refusal would name
the model that holds the contact. With the role, deleting a contact who
chairs, keeps the minutes of, scrutinises or received a ballot in an assembly
that is no longer a draft is refused, with the reason: archive it instead.

**Merging contacts that appear on a held list is refused.** Odoo's merge
wizard rewrites references in SQL, past every lock, and when both contacts
appear on the same list it deletes the duplicate line, with its ballots. So a
merge is refused when a contact that would disappear appears, as member or as
the person voting, on the list of a convened, open or closed assembly, for a
person who has the « Membres » role, with a message naming the contact and
the assembly. A draft or cancelled assembly does not block (its list is
rebuilt from the register), and the contact that is kept may appear on any
list. Without the role, the base module's check runs first, and any merge
that touches a contact on a voter list receives the same neutral refusal as
the base module, which names neither the contact nor the assembly.

After closing, the assembly's name, place and connection link stay editable,
tracked in the thread like the assembly's other tracked fields. The minutes
(text) and the attached documents stay editable too, and from the
convocation on every change is logged in the thread under the name of the
person who made it (« Procès-verbal modifié », « Pièce ajoutée », « Pièce
retirée », with the document's name): minutes are often adopted at the next
meeting.

From the convocation on, a document of the assembly went out with the notice:
its attachment box in the thread does not replace, detach or delete it,
outside superuser. Otherwise what the members received would change after the
fact, and a deletion would remove the document from the notice already sent.
It can be removed from the assembly's list of documents, which the thread
logs, and the new version attached next to it. Only documents that are still unattached and were uploaded by the
person writing are bound to the assembly: adding someone else's document id
to the list does not appropriate it. A document the person cannot read cannot
be attached at all, and the refusal does not name it: the email notice would
otherwise send it to the members. A convened or held assembly
cannot be deleted; it can be cancelled while still draft or convened, and
Odoo does not notify the members already convened.

**The state itself is never written directly**, not even by a manager. It
changes only through the buttons (« Convoquer », « Ouvrir l'assemblée »,
« Clore l'assemblée », « Annuler l'assemblée », « Remettre en brouillon »),
which make the checks and verify that the person clicking may write the
assembly. Each transition is then written with superuser rights; the context
key it uses has no effect on its own, so a client passing it in an RPC call
unfreezes nothing.

**What the assembly's thread keeps.** The assembly's tracked fields (name,
kind, date, place, link, company, chair, secretary, notice periods and date,
record date, admission of members in grace, quorum and proxy rules, state) go
through Odoo's tracking. The context keys that silence tracking
(`tracking_disable`, `mail_notrack`, `mail_create_nolog`) are ignored on the
assembly, at creation and on every change, transitions included, unless the
superuser itself is acting: otherwise an RPC client could change the date,
the place or the chair without the thread saying so. The module itself logs
in the thread, whatever those keys say: lines added or removed by hand,
changes of attendance, proxy holder and person voting, changes to a proposal
(title, text, mover, seconder, totals, majority), the sending of the email
notice (a count, without names) and, from the convocation on, changes to the
minutes and attached documents. The minutes and documents
of a draft assembly change without a trace.

## The legal framework, as default values

The defaults are those of the suppletive regime of the Civil Code of Québec,
which applies to non-profits under Part III of Québec's Companies Act. An
organisation's by-laws almost always replace them, so each one is a field.

| Rule | Default | Source the module cites | Where to change it |
|---|---|---|---|
| Notice period | 10 to 45 days | C.C.Q. art. 346 | Minimum and maximum notice periods. Canada Not-for-profit Corporations Act: 21 to 60 days, 21 to 35 by email |
| Financial statements with the annual notice | An annual assembly cannot be convened without at least one attached document | C.C.Q. art. 347 | Attach the statements (the module checks that a document is attached, not which one) |
| Quorum | A majority of the voters | C.C.Q. art. 349 | A number or a percentage (rounded up to the next person) |
| Proxy | Refused | C.C.Q. art. 350 (allowed unless the by-laws exclude it) | « Procurations permises » and the cap |
| Voting | Show of hands; secret ballot on request | C.C.Q. art. 351 | Voting method, per proposal |
| Record date | To be set | Canada Not-for-profit Corporations Act (the day before the notice, failing a board decision) | Field on the assembly; required to build the list |
| Remote meeting | Allowed | Companies Act, ss. 89.2 to 89.4 | Connection link; attendance « À distance » |

⚠️ These references are a configuration aid, not legal advice. Check them, and
your by-laws, with counsel before relying on them.

The notice date cannot be in the future (convene on the day the notice goes
out), and the notice period is counted on the meeting's local day: an
assembly at 8:30 p.m. in Montréal falls on the next day in UTC, the time zone
Odoo stores, and counting on the UTC day would accept a 9-day notice for a
10-day minimum. The time zone is the company's, otherwise the user's,
otherwise `America/Toronto`.

## No individual electronic voting

This release makes nobody vote online, by choice. A secret electronic vote is
acceptable only if it is verifiable and the organisation cannot know who voted
for what. A ballot cast online enters the database one at a time: one would
have to guarantee that neither the order of identifiers, nor the creation
time, nor any logging column, nor the server log can tie it to the person who
cast it. `bf_property_governance` shows what that takes (hashed receipts, a
ballot box created in one go and shuffled, logging columns removed) and what
remains even so (in a weighted vote, a ballot's weight can identify its
author). Until that work is done here, a secret ballot is held on paper, and
Odoo keeps its register and its totals.

## The minutes

The « Procès-verbal (PDF) » button, shown once the assembly is open, prints
what Odoo knows (the same report is in the Print menu at any time, from
draft on): the notice date and period, the record date, the number of
voters, attendance (on site, remote, by proxy) with each proxy holder, the
quorum, each proposal with its mover, seconder, voting method, required
majority, totals and result, then the text written by the secretary, with
signature lines for the chair and the secretary. For a secret ballot it shows
only the number of ballots handed out and the totals, which is all the
database holds.

## What it does not do

* **It sends nothing by itself**, neither the notice nor the minutes.
* **It does not vote online** (see above).
* **It does not track a members' requisition** of a special meeting; a special
  assembly is created and convened like any other.
* **It does not print paper notices**: it lists the members to notify by post,
  with their addresses, for export.
* **It does not keep the corporate register.** The bridge to the resolutions
  of `bf_corporate_governance` is `bf_membership_assembly_governance`,
  installed automatically when both modules are present.

## Security and access

| Group | What it can do |
|---|---|
| « Agent » | Hold the assembly: create it, build the list, convene, record attendance and proxies, hand out ballots, enter results, open and close. Cannot delete an assembly |
| « Responsable » (manager) | Everything an agent does, and delete an assembly that is still draft or cancelled |

An employee with neither role sees no assembly: the voter list and attendance
are personal information. All four models carry a per-company global record
rule, not only the assembly, because a voter line or a ballot can be read
without going through it. Documents attached to an assembly are bound to it,
so every agent who can read the assembly can read them.

## Requirements

Odoo 18 Community. `depends`: `bf_membership`, `mail`. No external Python
dependency beyond what Odoo already requires.

## Installation and configuration

1. Install `bf_membership_assembly` (it pulls in `bf_membership`); it adds
   « Membres > Assemblées ».
2. Make sure voting categories are marked as such, that member organisations
   have a voting delegate in office, and that members who agreed to email
   notices have « Accepte les avis par courriel » ticked.
3. Set the company's language: the notice is written in it.
4. For each assembly, set the notice periods, quorum and proxy rules from your
   by-laws before convening.

## Tests

The tests play access in the intended role (agent, manager, employee without a
role, another company) rather than as administrator. No email leaves a test:
convening is checked without creating anything, and the composer's send stays
in the queue, rolled back with the transaction. They cover the record date,
withdrawals and grace, the one-line organisation, notice bounds and the local
day, the composer, its language and autofollow, the fixed notice channel,
proxy chains and caps, quorum rounding, majorities at their bounds, the
secret-ballot register and its introspection, ballots and counts once a
result is entered, the state and the computed fields that cannot be written,
lines added or removed by hand (eligibility, computed grace and channel,
logging), the majority fixed once a result is entered and still fixed after
the totals are cleared, rule changes logged, what was put to the vote fixed
at the first count and amendments logged, attendance, proxy and
representative changes logged, minutes and document changes logged, documents
of a convened assembly that cannot be replaced or deleted, someone else's
private document refused, the notice date set by the convocation only, a
consent withdrawn before sending honoured, one email per member with its
documents and no shared message naming the recipients (neither from the
portal nor for a convened employee without the role), the notice sent only
from a convened assembly and only to the « Courriel » lines, a « Courriel »
line removed from the recipients moved to post and named, the sending date on
each line, a resend to one member that leaves the others notified, the proof
of notice cleared when the assembly goes back to draft, no notice through a
mass mailing, the notice template refused on every other path, the proxy cap counted per person (also when the person voting for
an organisation changes, and again at opening), an officer's deletion refused
with its reason for the role, the voices in the room checked again at
closing, no proxy from an
organisation without a delegate, a rebuilt list that names who it removes,
contacts held by an assembly archived rather than deleted (with a note in the
thread), tracking keys from a client ignored, lines and proposals that cannot
move, the refused merge (named with the role, neutral without it), and every
freeze.

```bash
odoo -d <database> -u bf_membership_assembly --test-enable \
     --test-tags /bf_membership_assembly --stop-after-init
```

## Known limitations

What remains true, and is assumed:

* **The notice sending date (`notice_sent_at`) is empty for notices sent
  before this field existed.** The emails of those sendings were deleted once
  sent: no migration can fill it.
* **A postal notice has no sending date.** The module lists the postal
  notices, with addresses; proof of mailing is kept outside Odoo.
* **Only the convocation notice goes out member by member.** A message written
  by hand in the assembly's thread, with recipients, follows Odoo's behaviour:
  each of its recipients can read the list of the others.
* **The check on voices in the room runs at entry and at closing**, not in
  between: attendance removed after a count makes it lie until closing, which
  then refuses to close.
* **A withdrawal recorded after « Bâtir la liste des votants » does not remove
  the person by itself**: the list is rebuilt while the assembly is convened;
  once open, the chair records it in the minutes.
* **An archived scrutineer stays linked to the proposal but is no longer
  shown** in the list of scrutineers (Odoo hides archived contacts in such a
  list). The chair and secretary are always shown.
* **The notice text ships in French** and uses its translation in the
  organisation's language when one exists; dates follow that language.
* **The secret ballot is held on paper** (see above), and a members'
  requisition of a special meeting is not tracked.
* **The legal references cited must be checked with counsel.**

## Changelog

- **18.0.1.0.3**: first public release.

## License

Business Source License 1.1. The manifest says `Other proprietary` because the
manifest schema has no BUSL value; the `LICENSE` file governs.

* Licensor: Les services de consultation Blue Fox, Inc.
* You may use the module in production for your own internal business
  operations. Providing it to third parties as a product or service (hosted,
  managed or resold) requires a separate written agreement with the Licensor.
* Each version converts to LGPL-3.0-or-later at its Change Date; for this
  version, `LICENSE` sets 2030-10-01.

See `LICENSE` for the full terms.
