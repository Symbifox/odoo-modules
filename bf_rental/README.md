# Residential lease (`bf_rental`)

First module of the rental strand. It grafts onto the neutral base of the suite
(`bf.property.organisation`, `bf.property.building`, `bf.property.unit`) and adds
no field to any co-ownership module.

## What it does

| Model | Purpose |
|---|---|
| `bf.rental.lease` | A lease of a dwelling: lessor, building, dwelling, lessees, the mandatory form used, term, rent, restrictions on the right to have the rent fixed, and the signed form as an attachment |
| `bf.rental.abandonment` | A lessee's departure without notice under art. 1975 CCQ, and the rules on effects left behind (arts. 944 to 946 CCQ, through art. 1978 CCQ) |

The lease records:

- the **lessor** (an organisation of the suite), the building, and optionally the
  dwelling (left empty for a room or a lot that matches no registered unit);
- one or more **lessees**, and whether they are bound **solidarily**. That box is
  copied from the form as ticked; it is never inferred from the number of
  lessees;
- the **form used** (Schedules 1 to 5 and 7 of the regulation), from which the
  module computes the letter of the section carrying the restrictions and the
  notice to a new lessee;
- whether it is the lease of a **room** (art. 1892 CCQ), which later changes the
  notice periods (art. 1942 para. 3 CCQ);
- the **term**, fixed or indeterminate, with a start date and, for a fixed term,
  an end date;
- the **rent**, the cost of services, the total (rent plus services, computed),
  the period (month or week) and the payment method;
- whether the lessee **consents** to give postdated cheques;
- the **restriction on the right to have the rent fixed** (art. 1955 CCQ): its
  ground (new building or change of use), the date the building was ready for
  its intended use, and the maximum rent for the five years;
- the signed form, as attachments, and an internal note. A file uploaded on a
  new lease is attached to it on save, so every manager can read it, not only
  the person who uploaded it. Only orphan files uploaded by the caller are
  attached: an attachment id slipped into the relation over RPC does not carry
  someone else's file into the lease, or into the lessee's portal.

References come from a sequence (`BAIL/<year>/0001`). The lease and the
abandonment record carry the chatter; the lease also carries activities.

## What it does not do, and why

**It does not produce the lease.** A residential lease in Québec is concluded on
a mandatory form of the Tribunal administratif du logement (CQLR, c. T-15.01,
r. 3, s. 1: the lessor must use the Tribunal's form), and every one of the
twenty form pages published in the regulation carries the footer *Reproduction
interdite*. That is not engineering caution: it is printed in the regulatory
text published by the Québec Official Publisher.

The lessor buys the form from the Tribunal (CAD 2.99 for the printed duplicate,
or the Tribunal's own electronic lease at the same price). This module records
what was agreed, states which form was used, and carries the signed document as
an attachment. It never recomposes it. A test holds that the module ships no
report that would reproduce the form.

What the suite *may* produce is the **notices**, and the distinction is in the
regulation's own title: *Regulation respecting mandatory lease forms **and the
particulars** of the notice to a new lessee*. For the lease, the law prescribes
the **medium**; for the notice, it prescribes the **content**. Notices get their
own module (`bf_rental_notice`).

**It carries no security deposit field.** Art. 1904 para. 2 CCQ: the lessor may
not exact any sum of money other than the rent, in the form of a deposit or
otherwise. This is ordinary everywhere else in North America and forbidden here;
carrying the field, even empty, invites its use. A test guards that absence, so
it breaks loudly if someone adds one.

**It refuses low-rental housing.** That is a separate regime: rent is fixed under
the regulations of the Société d'habitation du Québec rather than the ordinary
criteria (art. 1956 CCQ), and the lessor keeps a register of applications and an
eligibility list (art. 1985 CCQ). Choosing Schedule 2 raises an error that says
so, rather than silently applying the wrong rules. The form itself confirms the
boundary: Schedule 2 has neither a "restrictions" section nor a "notice to a new
lessee" section, because neither mechanism exists in that regime.

**A syndicate of co-owners cannot be the lessor.** The lessor of a rented
fraction is its owner; the syndicate administers the immovable and holds no title
to the private portions. The syndicate already records the lessee in its
register (art. 1070 CCQ) through the "rented" box of the fraction, which belongs
to the core module. A lease whose organisation is a syndicate is refused.

**It shows no "legal rent increase percentage"**, because none exists. The
regulation states criteria the Tribunal applies *if seized*, not a norm binding
on the parties in advance.

## What the lease refuses

- A lease with **no lessee**. This is checked in `create` itself, not only in a
  constraint: `@api.constrains` does not fire when the field is absent from the
  values, and `required=True` on a many2many only guards the screen. An import
  by RPC is exactly the path that would slip through. Emptying the lessees
  afterwards is refused too.
- A **fixed-term** lease without an end date, an **indeterminate** lease with
  one, and an end date on or before the start date. The two kinds do not count
  notice periods from the same point, so an inconsistent lease would get the
  other regime's periods.
- A **restriction** on rent fixing that would not be opposable: without its
  ground, without the date the building was ready, or, for a lease concluded
  after 20 February 2024 on a building ready after that date, without the
  maximum rent for the five years (art. 1955 para. 3 CCQ, as worded in Schedule I
  of CQLR, c. T-15.01, r. 1.1). A restriction recorded but unenforceable would
  read as acquired the day someone relied on it; the module refuses it instead.

`create` also copies the caller's values before filling in the reference, so a
caller reusing one dictionary for two leases does not hand the second one the
first one's number.

## The form used is not a label

The regulation's forms do not share a structure. Schedules 3, 4 and 5 have nine
sections, A to I; Schedule 1 has eight and its section G is titled "Notice to
the new student"; Schedule 7, the written statement for a verbal lease, has five
and shifts everything by one.

So "section F" means nothing on its own. The restriction is declared in F on four
forms, but in **D** on the verbal-lease statement, and Schedule 2 has no such
section at all. The module therefore models the **nature** of the section and
computes the letter from the form in use. The letter only ever serves to write
"see section X" for someone holding the paper; it never decides a rule.

## Postdated cheques are not the deposit

The verb in art. 1904 CCQ is *exact*. The lessor may not impose a postdated
instrument; the lessee may consent to one, and the official form carries the box
together with the lessee's initials. The field therefore exists, off by default,
and what it records is a **consent**, never a requirement.

## Departure without notice: what the lessor may NOT do

This is the corner of residential leasing where a generic module does the most
damage, because the obvious move ("the lessee left, let's clear the dwelling")
is almost certainly unlawful.

**Art. 1975 CCQ** carries **two cases that are not alike**. Abandonment without
reason, *taking one's movable effects*, resiliates the lease **of right**. A
dwelling **unfit for habitation** that the lessee leaves without notice **may**
be resiliated. Automatic versus optional: a single "lessee gone" checkbox would
conflate them, and would err in the direction that strips someone of their
lease. The record therefore asks which case applies, and computes "lease
resiliated of right" only for the first one with no effects left.

**And "taking one's movable effects" is not decorative.** If the lessee leaves
their belongings, art. 1975 para. 1 CCQ does not apply: the lease is *not*
resiliated of right, and art. 1978 CCQ sends the lessor to the rules on the
**holder of property entrusted and forgotten**. The module refuses to call a
departure an abandonment when effects were left.

Those rules are long, and carrying them is the point of this model:

- **Art. 944 CCQ**: property is "forgotten" only after **90 days** unclaimed, and
  the holder may dispose of it only **after giving notice of the same duration**.
  So 90 days, and 90 days' notice. The two may run **concurrently**; they do not
  add up. Inventing 180 days would be as wrong as inventing 90. The module
  computes the date from which the effects are deemed forgotten and the date
  before which no disposal is allowed (the later of the two periods), and
  computes the latter only once a notice has been given.
- **Art. 945 CCQ**: disposing means **selling** (by auction, or by agreement),
  failing that **giving to a charity**, and only failing that, disposing freely.
  The disposal methods offered follow that order; a test asserts that neither
  "throw away" nor "destroy" appears among them.
- **Art. 946 CCQ**: the owner may **claim** the property while their right is not
  prescribed, and if it has been sold their right attaches to **the proceeds**.
  The lessor never becomes owner: they hold, they administer, and they owe an
  account. The record carries the proceeds of the sale.

A disposal date is refused when no notice was given, or when it falls before the
computed date. The error is irreversible (belongings sold cannot be given back),
so the refusal is firm. The module never says "you may dispose": it computes the
date before which the answer is certainly no, and the form states what disposing
means.

## Security

| Group | Leases and departures |
|---|---|
| Consultation | Read |
| Manager | Read, create, write, delete |

- Multi-company record rules on both models: a manager of another company does
  not see the leases, names, addresses and rents of this one. Records with no
  company stay visible, as everywhere else in the suite.
- The lease's free-text **note** is restricted to internal users of the property
  groups. Its content is unpredictable by construction (what a manager writes
  about a lessee is not meant for the lessee), and a read access rule applies to
  the model, not to what a portal template happens to display.
- No mail template, no server action, no scheduled action. The module subscribes
  nobody. A lessee added as a follower receives a message only when someone
  deliberately posts one, and being a follower grants no read access to the
  model.

Menus: *Leases* and *Abandonments of the dwelling*, under the people menu of the
property suite.

## Sources

Civil Code of Québec, CQLR c. CCQ-1991, arts. 944 to 946 and 1851 to 1995.
Regulation respecting mandatory lease forms and the particulars of the notice to
a new lessee, CQLR c. T-15.01, r. 3. Regulation respecting the mandatory content
of the notice of modification of a lease of a dwelling, CQLR c. T-15.01, r. 1.1.
All read against the official consolidated text, current to 7 April 2026.

## Dependencies

- `bf_property_core`

## Tests

38 tests, of which the refusals are the spine: the absent deposit field, the
absent lease report, low-rental housing, the syndicate as lessor, the lease
without a lessee (at creation and by emptying), the duration and restriction
checks, the multi-company wall, the sequence, and the abandonment and disposal
rules.

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
