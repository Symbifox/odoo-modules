# Rental notices (`bf_rental_notice`)

The other half of the dividing line. The lease is not reproduced: the Tribunal's
form is sold and carries *Reproduction interdite* on every page. Notices are
different: the law prescribes their **content**, not their medium. That is
written in the regulation's own title, *Regulation respecting mandatory lease
**forms** and the **particulars** of the notice to a new lessee*.

This module records the notices given under a lease (`bf_rental`), computes the
periods within which each must be given and answered, states what the lessee's
silence will produce, and handles resiliation by the lessee. It does not yet
print the notice document itself.

## What it does

| Model | Purpose |
|---|---|
| `bf.rental.notice` | A notice given under a lease: its kind, the dates it was given, received and aimed at, the effect of silence, the deadlines, and its status (draft, given, accepted, refused, lapsed) |
| `bf.rental.moratorium` | The recorded state of the eviction moratorium: its basis, whether it is in force, its statutory end, any notice published in the *Gazette officielle*, the effective end, when and where it was checked, and the territories carved out |

Notice kinds: modification of the lease (including a rent increase),
repossession, eviction (subdivision, enlargement, change of use), end of lease
after a sublet of more than 12 months, offer of a new lease in a private seniors'
residence, the two notices to a new lessee (last rent paid, maximum rent over five
years), and resiliation by the lessee.

References come from a sequence (`AVIS/<year>/0001`). The status is set by hand;
the module does not move a notice to accepted or lapsed on its own.

## Silence does not mean the same thing twice

This is the sharpest trap in the whole corpus. A module treating "no answer" one
way is wrong **three times out of four**.

| Notice | One month of silence | Effect |
|---|---|---|
| Modification of the lease | art. 1945 CCQ | **Acceptance** |
| Repossession or eviction | art. 1962 CCQ | Refusal to leave |
| End of lease after more than 12 months of sublet | art. 1944.1 CCQ | Refusal to leave |
| Offer of a new lease in a seniors' residence | art. 1959.2 CCQ | Refusal of the offer |

The notices to a new lessee and the lessee's resiliation expect no answer: the
first informs, the second exercises a right the lessor cannot refuse. Their
silence effect is "none", and they get no response deadline.

The one-month response deadline runs from **receipt**. Until the date of receipt
is known, no deadline is shown: a deadline computed on an assumed date is worse
than none.

Art. 1945 CCQ carries its own exception: where the lease concerns a dwelling
under art. 1955 CCQ, the lessee who refuses the modification **must leave at the
end of the lease**. Refusing is not staying. The module raises that flag on a
modification notice when the lease carries a restriction on rent fixing.

When the lessee refuses a modification, the notice computes the date by which
the lessor must apply to the Tribunal: one month from the refusal, failing which
the lease is renewed of right on the former conditions (art. 1947 para. 2 CCQ).
It is the only deadline in the corpus that runs against the lessor.

## Deadlines are calendar months, not blocks of days

Converting everything to days and comparing durations goes wrong in two silent
ways:

- A lease from 1 July to 30 June is 364 days, which is 11.96 months by division,
  therefore "less than 12 months", therefore the wrong notice regime. That is the
  commonest lease in Québec.
- "Three months before 30 June" is 30 March, not "91.32 days before". The gap
  moves the deadline by two days depending on which months are crossed.

Everything is computed with `relativedelta` on real dates, the end date of the
lease counts as included, and the bounds are **dates**, never day counts.

The two articles do not cut at the same place: art. 1942 CCQ separates leases at
**12 months**, art. 1960 CCQ at **6 months**, and art. 1960's threshold is
inclusive on the short side (six months *or less* takes the one-month notice).
Art. 1942 has a **maximum** as well as a minimum: a modification notice given too
early is refused just like one given too late. Art. 1960 has only a minimum.

The periods run towards the end of the lease for a fixed term, and towards the
proposed date for an indeterminate lease. A **room** counts in days (10 and 20)
rather than months (art. 1942 para. 3 CCQ).

The maximum-rent notice to a new lessee is refused on a lease that carries no
restriction on rent fixing (art. 1955 para. 3 CCQ).

## The moratorium is not a date

Eviction for subdivision, enlargement or change of use is suspended (CQLR,
c. D-13.01, s. 1). Everyone remembers "until 6 June 2027"; that is a
**ceiling**, not a deadline. Section 11 of that Act ends it **sixty days after a
notice published in the *Gazette officielle***, once the vacancy rate reaches
3 %, and section 2 lets the government carve parts of the territory out of it.

The module therefore holds a **recorded state**, with its source and the date
somebody went and looked, never a constant. The effective end is the earlier of
the statutory end and the sixtieth day after a *Gazette* notice (sixty days, not
two months). An eviction notice given before that end, while the recorded state
is in force, is refused with a message that states when the state was checked
and why it may have ended earlier. When no state is recorded, the module blocks
nothing: imposing its own ignorance as if it were the law would be worse than
saying nothing.

The module ships with the state known at delivery (statutory end of 6 June 2027,
the source consulted, the date it was checked). That record is loaded once and
never rewritten by an update: from then on it belongs to whoever keeps it
current. The moratorium has no company rule, on purpose: the state of a Québec
statute does not depend on which company reads it.

## Resiliation by the lessee: three grounds, three attestations

The Code gives the lessee exits the lessor cannot refuse. They do not resemble
one another, and conflating them costs twice.

**Art. 1974 CCQ**: low-rental housing allocated, rehousing ordered by the court,
a **handicap** preventing occupancy, or, for an **elderly person**, permanent
admission to a residential and long-term care centre, an intermediate resource,
or a private seniors' residence offering the nursing care or personal assistance
their condition requires.

**Art. 1974.1 CCQ**: **sexual violence, spousal violence, or violence against a
child** living in the dwelling, where safety is threatened.

**Art. 1976 CCQ**: a lease accessory to a contract of employment, once that
contract has ended.

**The attestation is not the same, and that is the central trap.** Under
art. 1974 CCQ it comes from the authority concerned, with, for an elderly person,
the certificate of an authorised person. Under art. 1974.1 CCQ it comes from **a
public servant or public officer designated by the Minister of Justice**, on the
strength of a judgment **or a sworn statement** by the lessee. A module demanding
a medical certificate from a victim of spousal violence would ask for a document
the law does not require, from the person least able to obtain it. The notice
computes, from the ground, who must attest, in the reader's language.

A given notice (not a draft) with an art. 1974 or 1974.1 ground is refused until
the attestation is marked as received; a draft may be prepared before it arrives.
The employment ground needs none. A resiliation notice must state its ground, and
a ground on any other kind of notice is refused.

**The date it takes effect** is computed from **sending**, not receipt (the
opposite of the art. 1945 response period): two months, or one month when the
lease is for less than 12 months or of indeterminate duration, and always one
month for the employment ground. It takes effect earlier if the parties agree on
an earlier date or if the dwelling, vacated, is **relet** during that period; the
module keeps the earliest of those dates. A relet date before the notice was
given is refused.

**The art. 1974.1 ground is information of particular sensitivity.** It reveals
that someone is a victim of violence. The ground field is therefore **not
tracked**: tracking would publish it to the chatter and push it to every
follower. A test guards that decision, and the form shows a warning (do not copy
it into an email, a chatter note or an export) instead of letting it travel.

### Who may read that someone is a victim

The Consultation group reads the whole suite, so it is broad. A resiliation
notice must be handled by someone, but knowing that a person is a victim of
violence is necessary to nobody except whoever handles that document.

The three fields carrying the ground (the ground itself, the expected attestation
authority, and the sensitivity flag) are therefore restricted to the Manager
group **together**, because restricting one alone would achieve nothing: the
authority "designated by the Minister of Justice" names only art. 1974.1, and a
flag true for that single case *is* the information in another shape. On the
form, the whole resiliation section is hidden from non-managers.

The restriction covers the **ground**, not the notice. That the lease is
resiliated and on what date stays readable; it is the *why* that is nobody
else's business. Tests hold that boundary in both directions.

## Security

| Group | Notices | Moratorium |
|---|---|---|
| Consultation | Read (without the resiliation ground) | Read |
| Manager | Read, create, write, delete | Read, create, write, delete |

- Multi-company record rule on notices; none on the moratorium (see above).
- The free-text **note** of a notice is restricted to internal users of the
  property groups, for the same reason as on the lease.
- The *Eviction moratorium* menu is shown to managers only; *Notice* sits under
  the people menu of the property suite.
- No mail template, no scheduled action.

## What lives elsewhere

**Art. 1975 CCQ, abandonment of the dwelling**, is a state of the lease, not a
notice. It is modelled in `bf_rental` (`bf.rental.abandonment`).

## Dependencies

- `bf_rental`

## Tests

68 tests, covering the silence table, the notice windows and their thresholds,
the moratorium, the content rules, the multi-company wall, and resiliation by the
lessee (grounds, attestations, effective date, sensitivity and who may read it).

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
