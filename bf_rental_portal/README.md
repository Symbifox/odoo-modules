# Residential lease: lessee portal (`bf_rental_portal`)

The lessee finds their lease, their notices and what they have paid without
having to phone. Three pages, read only, on the standard Odoo portal. On the
office side, an "Invite to portal" button on the lease opens the portal to the
lessees with the lessor's own invitation.

## What it does

| Page | What it shows |
|---|---|
| `/my/rental` | Each lease the person is party to: the dwelling, the lessor, the term, the rent and its services, the restriction of art. 1955 CCQ when it applies, and the signed form |
| `/my/rental/notices` | The notices received, the deadline to answer each, and what silence will produce |
| `/my/rental/rent` | Rent terms one by one: due, paid, outstanding, and their state |
| `/my/rental/lease/<lease>/form/<attachment>` | Downloading the signed form of their own lease |

The portal home page gets three cards (my lease, notices received, my rent),
with counters for leases and for notices still in the "given" state.

| Model | Extension |
|---|---|
| `bf.rental.lease` | The "Invite to portal" button, the subject and portal button of its emails, and three stored names the lessee may read |
| `bf.rental.notice` | The selection of notices whose response period is still running |
| `bf.rental.term` | The outstanding amount on terms already due |

## Membership is read from the lease, never from the fraction

The co-ownership portal resolves its audience through the occupant recorded on
the fraction, for the register of art. 1070 CCQ. Reusing that door here would
be wrong three times over:

1. **Co-lessees would disappear.** A lease has several lessees; the fraction
   carries one occupant, the one the syndicate recorded. The second signatory
   would not see the lease they signed.
2. **Leases without a fraction would disappear.** A room, or a lot for a mobile
   home, matches no recorded fraction.
3. **And the worst, the other way round: someone would see another person's
   lease.** A person recorded as the occupant of a fraction without being party
   to the lease (a separated spouse left in the register, a former lessee never
   removed) would get in.

The lease says who the lessee is. Every record rule of this module reads
`tenant_ids` on the lease (or on the lease a notice, a term or a payment belongs
to), and a test proves that the occupant recorded on the fraction gets an empty
page.

**An ended lease stays readable by its lessee.** No date filter: a former
lessee may need their lease for a procedure. What closes is the right to act on
it, not the right to read it.

## Opening the portal to the lessees

The lease form carries an **"Invite to portal"** button, shown to the property
manager group. It invites every lessee of the lease through the core helper
`res.partner._bf_property_grant_portal`, with this module's template:

- **The invitation is the lessor's.** Its subject and body name the lessor's
  organisation and list what the portal holds (the lease and its documents, the
  notices, the rent and what remains to pay); it does not use Odoo's generic
  portal invitation.
- **Someone already invited gets their access re-sent**, with a fresh link to
  choose a password.
- **Someone without an email address, or who holds an internal account, is set
  aside**, never converted.
- **The outcome is posted on the lease's thread** and shown in a banner.
- A lessee cannot invite anyone: the helper refuses a caller outside the
  manager group.

Emails posted from a lease carry a subject that names the lessor as well as the
lease reference, and a lessee with a portal account gets a button to
`/my/rental`. Those emails, like the invitation, use the branded layout of the
Symbifox branding module when it is installed, and Odoo's light layout
otherwise; the branding module is not a dependency.

## The portal opens nothing on the register

The portal group gets **no** access right on `bf.property.organisation`,
`bf.property.building` or `bf.property.unit`. Those models do not carry only
names: the organisation carries its arrears and fund balances, the fraction
carries its owners, its share and what its owner owes. None of that belongs to
the lessee, and a read ACL given "to display a name" gives the whole model; the
template is not the guard.

So the lease carries three **stored** related fields (lessor name, building
name, dwelling name) and the page reads those. Stored is not a performance
detail: an unstored related field reads through, at display time, with the
reader's rights, and the page would fail again.

**No grip on attachments either.** The portal group has no right on
`ir.attachment`, on purpose: opening it to show a file name would open every
attachment in the database. The controller builds the list of signed forms
under `sudo` only after the lease itself was found under the reader's rights,
and the download route checks two things: that the lease is the reader's, and
that the requested attachment belongs to that lease. Without the second, a
guessed id would serve any attachment to anyone holding some lease.

## What the pages refuse to say

**They never announce an eviction.** Only the tribunal resiliates (art. 1971
CCQ: the lessor "may obtain"), the three-week threshold only touches the
tribunal's discretion (art. 1973 CCQ), and paying before judgment stops
everything (art. 1883 CCQ). A frightening screen would say three false things.

**They never show the ground of a resiliation under art. 1974.1 CCQ.** Sexual
violence, domestic violence, violence toward a child: the field is reserved to
management and out of reach of the portal, and it has no place on a screen read
in a kitchen, in front of anyone.

**They never total the rent still to run.** Art. 1905 CCQ deprives of effect a
clause making the whole rent exigible on default; displaying it would claim by
screen what cannot be claimed in law. What is shown is the balance term by
term, and the total covers only terms already due and late or partly paid. A
term **deposited with the court** (art. 1907 CCQ) is shown as deposited, never
as owed: the lessee paid, elsewhere. A term falling due after the lease has
actually ended (the *rent due until* date the lessor enters) is shown as such,
with no balance claimed on its line, and is not counted either.

## What they do say, and what it costs to ignore

**The deadline to answer a notice, and what silence produces.** Silence is
acceptance on a modification of the lease (art. 1945 CCQ) and refusal on a
repossession or an eviction. A lessee who lets the month pass on a notice of
modification sees their lease renewed with everything that was asked. Pending
notices are shown first, at the top of the page, and a notice is pending only
while three things hold: it is still "given", its deadline is known, and that
deadline has not passed. The deadline runs from **receipt**; without a date of
receipt, no deadline is shown rather than one computed on a guessed date.

**When refusing means leaving.** On a dwelling covered by art. 1955 CCQ,
refusing a modification obliges the lessee to leave at the end of the lease
(art. 1945 para. 2 CCQ), and the page says so.

**The one deadline that runs against the lessor.** After a refusal, the lessor
must apply to the tribunal within the month, failing which the lease is renewed
of right on the former conditions (art. 1947 para. 2 CCQ). The page gives that
date.

**The restriction of art. 1955 CCQ.** When the lease carries it, the lease page
says that the lessee's recourse to have the rent fixed is restricted for the
first five years, which section of their form carries the mention, and the
maximum rent written there.

**A missing form.** When no signed form is on file, the page says the lessor
must give the lessee a copy within ten days of signing (art. 1895 CCQ). The
rent page recalls that the lessor must give a receipt on request (art. 1908
CCQ) and may exact no sum other than rent (art. 1904 CCQ).

**Nothing that depends on the day lives in a record rule.** An `ir.rule` domain
is cached without a time component, so a date written there would be evaluated
once and frozen. The pending-notice selection lives on the model, where a test
takes it head on.

## Design notes

**No dependency on the co-ownership portal, deliberately.** The co-ownership
portal serves co-owners and occupants of a syndicate; this one serves the
lessees of a lessor. An owner of rental buildings has no syndicate, and imposing
announcements, common-space bookings and the register of art. 1070 CCQ on them
would install obligations that are not theirs. The two portals coexist when both
are installed; neither needs the other.

## Security

- **Portal users**: read only on leases, notices, rent terms and payments,
  each restricted by a record rule to the leases the person is a lessee of. No
  write, create or delete; no access to organisations, buildings, fractions or
  attachments.
- **Signed forms**: the portal serves only the files ATTACHED to the lease
  (`res_model` and `res_id` pointing at it), not every file in its relation.
  Odoo checks no right on a file slipped into a many2many, so membership of the
  relation alone would let whoever can edit a lease pull any file of the
  database into a lessee's portal.
- **Notices a lessee never sees**: drafts, which are the office's work in
  progress, and every termination given by a lessee (art. 1974, 1974.1, 1976).
  Co-lessees share the lease, not the termination: under art. 1974.1 it is given
  by a victim of violence, and the co-lessee may be the aggressor. What would
  serve them is not the ground, which never reaches the portal anyway, but "she
  is leaving, and when". Nothing records which lessee gave it, so it leaves the
  portal for all of them; the one who gave it already knows.
- **Property managers**: the "Invite to portal" button.

## Dependencies

`bf_rental_notice`, `bf_rental_rent`, `portal`.

## Tested

Membership read at the lease (co-lessee, lease without a fraction, the occupant
recorded on the fraction who sees nothing, an ended lease still readable), the
portal unable to write or create, the art. 1974.1 ground and the management note
out of reach, the register and attachments closed, the pending-notice window
including its last day and a notice without a date of receipt, the outstanding
total, a term deposited with the court and a term after the end of the lease,
the three pages rendering, the form
route refusing another lease and another lease's attachment, the pages for a
room lease and for someone with no lease at all, and the invitation (by a
manager, the lessor's, with a subject naming the lessor and a portal button).

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations, which include administering immovables that you own or
  that you are constituted to administer, and letting a person acting on your
  behalf use your instance for that purpose. A lessor, a housing cooperative or
  a non-profit housing organisation running the module for its own immovable is
  covered, and so is the bookkeeper or the manager it hires who works inside its
  instance.
- **Requires a written agreement**: administering immovables for the account of
  others, and providing the module as a product or service to third parties,
  whether hosted, managed or resold.
- **Change Date**: on 2030-08-30, this version converts automatically to
  **LGPL-3.0-or-later**.

## Acknowledgements

Created and maintained by Les services de consultation Blue Fox, Inc. AI coding
assistants were used as productivity tools during development.
