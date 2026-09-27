# Co-ownership: occupant portal (`bf_property_portal`)

What the syndicate makes available to the people in the building, and to whom.
Announcements, documents, the contact details of the syndicate itself,
maintenance requests, bookings of the common spaces, and the parcel and visitor
logs, on the standard Odoo portal. And, on the office side, the gesture that
opens the portal to a resident and the notices that keep the office and the
resident informed of what the other did.

The models hang on `bf.property.organisation`, the neutral base of the suite, so
nothing in the code restricts the portal to a syndicate. The text below speaks
of the syndicate because the rules that shaped it come from the co-ownership
register.

## What it does

| Model | Purpose |
|---|---|
| `bf.property.announcement` | A notice to the building, with an audience and a visibility window |
| `bf.property.document` | A document made consultable, with a category that decides its regime |
| `bf.property.request` | A maintenance request filed by a resident, and its thread |
| `bf.property.booking` | A booking of a bookable common space |
| `bf.property.parcel` | The parcel log |
| `bf.property.visit` | The visitor log |
| `bf.property.organisation` (extended) | Portal contact details, the response commitment, the log retention period |
| `bf.property.unit` (extended) | The "Invite to portal" button, and who the portal lets in |
| `res.users` (extended) | The per-user preference "Portal filings by email" |

## Why it is not a generic notice board

Two provisions decide almost every design choice here.

**Art. 1070 CCQ** lists what the syndicate keeps in its register: minutes of the
general meeting **and of the board of directors**, written resolutions, the
by-laws of the immovable, the financial statements, the declaration of
co-ownership, contracts, the cadastral plan, plans and specifications, location
certificates, the maintenance log, the contingency fund study, and the
description of the private portions. The same article puts the name and address
of every co-owner and of every lessee in the register, and puts **other**
personal information there only with the express consent of the person
concerned.

**Art. 1070.1 CCQ** says how the register is consulted: in the presence of a
director or of a person designated by the board of directors, at reasonable
hours, and according to the by-laws of the immovable. A copy is obtained by a
co-owner **for a reasonable fee**.

Three conditions, then, and a fee. Putting a register item on a portal that is
open at all hours gives **more** than the article requires. That is not
unlawful, and a syndicate may well want it. It is a decision, and this module
makes the syndicate take it rather than taking it by default.

## What that produces

- **A document carries its regime, through its category.** Thirteen of the
  categories are register items under art. 1070 CCQ; the rest (notices,
  guides, forms, other) the syndicate disposes of freely. The flag is computed,
  not typed.
- **Publishing a register item is refused until it is acknowledged.** The
  refusal quotes art. 1070.1 CCQ, and the publication is recorded in the log
  with the audience it was given.
- **Changing the category of a published document unpublishes it** when it
  becomes a register item that nobody acknowledged. Without that, a notice
  turned into minutes would stay online with no decision behind it.
- **A reader who is shown a register item is told so**, and told what
  art. 1070.1 CCQ would otherwise require.

## The lessee is not the co-owner

A lessee is *in* the register (art. 1070 para. 1 CCQ, name and address). That
gives no right *to* the register, which art. 1070.1 CCQ reserves to the
co-owner. So every announcement and every document carries an audience:
co-owners, occupants, or both.

**The partition is enforced by record rules, not by the views.** An
`invisible` attribute hides a field on a screen; it protects neither an ORM
read nor the route that serves a file. A lessee who guesses a document id gets
nothing, and a test proves it over HTTP rather than through the ORM alone.

**The share of common expenses is the co-owner's.** On the home page, the
relative-value column is filled only for the fractions the reader owns; an
occupant who rents sees it empty rather than the figure of their lessor.

**No anonymous links.** No `portal.mixin`, no access token. A register item
served by a URL that can be forwarded would hand to anyone what art. 1070.1 CCQ
gives to a co-owner under conditions. A session is always required.

**No directory of co-owners.** The portal shows how to reach the syndicate (a
contact name, email, phone and free text the syndicate writes itself), and
nothing about the other occupants.

## A rule domain carries no date

The visibility window of an announcement (published, started, not yet expired)
is applied by the **controller**, on every request, and never by a record rule.

An `ir.rule` domain is cached by `ormcache` on `(uid, su, model, mode,
companies)` with **no time component**: a date written into `domain_force` is
evaluated once and then frozen until the cache is invalidated. The same trap
has already been paid for elsewhere in this repository, where an attachment
stayed readable past its own expiry date. The rules here answer only a question
that does not depend on the hour: does this person have a current link to this
syndicate, and in what capacity.

## Access granted to a portal user

Read only, and bounded by its own rules: the fractions they own or occupy, the
buildings and syndicates that carry them, their own entry in the ownership
register, the common spaces of their buildings, and the announcements and
documents whose audience covers them. Two doors lead in, and they do not open
onto the same thing: a **current entry in the ownership register** makes a
co-owner, the **occupant recorded on a rented fraction** makes an occupant. The
same person can be both, for different fractions, and the role is computed
syndicate by syndicate.

These reads are not decoration. A rule that **traverses** (through the
syndicate's fractions to their ownership entries) is evaluated with the
reader's own rights, so reading an announcement requires being able to read the
syndicate, the fraction and the register entry crossed on the way. Without them
the page answers 403, which is exactly how the HTTP test found the omission.

## Opening the portal to a resident

The fraction form carries an **"Invite to portal"** button, shown to the
property manager group. It invites the current co-owners of the fraction and,
when it is rented, its occupant. The work is done by the core helper
`res.partner._bf_property_grant_portal`, with this module's invitation
template:

- **The invitation is the syndicate's.** Its subject and body name the
  syndicate and list what the portal holds (announcements and documents,
  requests, bookings, parcels and visitors); it does not use Odoo's generic
  portal invitation, which a hosting company may have rewritten for its own
  clients.
- **Someone already invited gets their access re-sent**, with a fresh link to
  choose a password. That is also the office's answer to "I lost my password".
  The link opens the page where a password is chosen, not a bare login page.
- **Someone without an email address, or who holds an internal account, is set
  aside**, never converted. The portal is not a door into the back office.
- **The outcome is posted on the fraction's thread** (invited, re-sent, set
  aside, by name) and shown in a banner.
- A resident cannot invite anyone: the helper refuses a caller outside the
  manager group.

The welcome sent after the person chooses a password is also the syndicate's,
not Odoo's generic "account created" message; that substitution lives in
`bf_property_core` and applies only to people the suite invited.

## Maintenance requests

An occupant reports something; the syndicate answers on the same thread. Not to
be confused with the **maintenance log** of art. 1070.2 CCQ and its regulation,
which is a statutory document established by an independent professional. This
is a ticket about a garage door that squeaks.

**Who bears the cost is computed, and only from what the text says.**
Art. 1064 CCQ has three regimes: a general common portion falls on every
fraction in proportion to its relative value; current maintenance and repairs
of a restricted-use common portion fall on the co-owners who have the use of it
alone; **major repairs and replacement of that same restricted-use portion
follow the general rule** and fall on every fraction, absent a clause in the
declaration. The module displays that reading with its article. It invoices
nothing: allocation lives in the budget.

**A request about a private portion is not silently accepted as the
syndicate's business.** Art. 1039 CCQ gives the syndicate its object: the
conservation of the immovable, the maintenance and administration of the common
portions, the safeguard of the rights attached to the immovable, and operations
of common interest. A request about a private portion is shown against that
object rather than being quietly costed to everyone.

**What is deliberately not encoded.** Access to a private portion to carry out
works, the notice that must precede it, and the indemnity for any damage: none
of those provisions are carried by the project's sourced rulebook, and nothing
here is written from a recollection of their text. The module records that a
request concerns a private portion and asserts nothing about the access regime.
It is a question for legal review.

**The response deadline has no statutory source.** No provision obliges a
syndicate to answer an occupant within a number of days. The module claims no
legal deadline: the syndicate enters the commitment it gives itself, and the
module counts the days of that commitment. Left at zero, nothing is counted and
no lateness is shown.

**A thread does not close on nothing.** Settling a request, or refusing it as
outside the syndicate's object, requires saying what was done or why. That is
what somebody will read in two years looking for when the leak was repaired.

**The decision reaches the requester.** Settling or refusing sends the
decision and its reason by email to the person who filed the request ("Request
settled: ..." or "Outside the syndicate's object: ..."), posted on the thread
at the same time. Someone without an email address still finds it on the
thread. The subject names the syndicate as well as the reference, because a
person may own in one building and rent in another, and a portal user gets a
button to their own list of requests.

### Delays, measured so they can be grouped

Three stored fields turn the thread into numbers a report can average: the
delay to take charge, the delay to settle, and the result against the
commitment. Two rules keep them honest:

- **A refusal is not measured.** A refused request carries a closing date, and
  a delay computed on it would say "how fast does this syndicate say no". The
  three fields stay empty on a refusal.
- **No commitment, no result.** With no commitment entered, the result is
  "no commitment entered", not "met". A dashboard showing green by default
  would invent a performance.

### What a portal user may do

Create a request, and read their own. **Not the neighbours'.** A ticket often
describes somebody's problem, with their door number and their own words;
art. 1070 para. 1 CCQ puts a third party's personal information in the register
only with their express consent, and publishing everyone's threads would do the
opposite. The syndicate sees all of them from the back office.

**A `unit_id` posted by a browser is not proof of a link.** The controller
keeps only the fractions the person actually holds, and `create()` checks the
entitlement again on the server. The create is deliberately **not** run under
`sudo`, because that would make the model-level guard inert and leave the
controller as sole judge. Two things are sudoed, each for a stated reason: the
sequence (a portal user has no read access to `ir.sequence`, and without it the
very first request filed by an occupant fails on an ACL) and the opening chatter
message (a portal user cannot create a `mail.message` on their own authority),
whose author stays the requester, so the thread carries their name.

## The office is told when a resident files something

A request, a booking or a visitor filed on the portal **notifies the office and
subscribes it** to the new record. The office is the internal, active followers
of the syndicate's own record, which is where the syndicate says who follows its
affairs; failing any, the property managers of the syndicate's company, so the
first filing of a syndicate that has set nothing up is not lost. The person who
filed is never notified of their own message.

**Accounts that handle notifications in Odoo get a per-user choice.** A
safety request (a smell of gas) must not wait in an Odoo inbox until somebody
opens it. The user preference **"Portal filings by email"**, shown only when
notifications are handled in Odoo and writable by the user themself, decides
what is *also* sent by email:

| Value | What is emailed |
|---|---|
| Safety requests only (default) | Requests flagged as a safety matter |
| All portal filings | Every request, booking and visitor |
| No email | Nothing beyond the Odoo inbox |

Accounts that receive notifications by email already get the ordinary
notification and are not affected. The alert uses its own template, "staff
alert", with the filing's subject and text and a button that opens the record
in Odoo.

## Booking a common space

The community room, the gym, the moving lift. Built natively, **not** on an
appointment module.

**The appointment module of the suite depends on `resource_booking`, which is
AGPL-3.** This suite is BUSL-1.1, and a declared dependency is no safer than a
copy: a module that `depends` on an AGPL-3 module and imports its models forms
a whole whose distribution triggers the copyleft. `resource_booking` is also
absent from the public repository, so the dependency would be unsatisfiable
there. And the shape does not match: `resource.booking` models an appointment
with a person on a resource calendar, not the room from 14:00 to 17:00 on
Saturday.

**Art. 1043 CCQ** distinguishes the restricted-use common portion, whose
enjoyment is reserved to certain fractions. The terrace attached to other
fractions is therefore not bookable by anyone else: the module refuses, and
names the fractions that hold it.

**The conditions of use come from the by-laws of the immovable.** The module
invents none: it carries the text the syndicate writes there, and recalls that
amending the by-laws falls under the majority of **art. 1096 CCQ**, which names
that case expressly. The caps (maximum duration, how far ahead) are settings,
not law, and the module says so.

**Nobody's name on the availability view.** An occupant sees that Saturday is
taken, never by whom. Art. 1070 para. 1 CCQ puts a third party's personal
information in the register only with their express consent; publishing the
building's social calendar would do the opposite. The syndicate sees everything
from the back office.

### Three traps this feature paid for

**A guard that reads with the guarded user's rights disarms itself.** Twice in
one file. The restricted-use check read `restricted_unit_ids` as the requester,
and the record rule on fractions hides the neighbour's fraction, so the list
came back empty and the space looked unrestricted. The overlap check searched
as the requester, and the rule on bookings hides other people's, so no clash
was ever found: the guard was inert for precisely the case it exists for, two
different people on the same room. Both now read under `sudo`.

**A constraint fires at flush, not at create.** A `try` wrapped around the
`create` alone misses it: the page redirects as if all were well and the clash
surfaces later. The portal writes therefore run inside `cr.savepoint()`, which
flushes on entry and on exit, so the constraint fires where it can be caught
and the rollback leaves nothing behind.

**A `datetime-local` input posts the browser's local time**, and Odoo stores
UTC. Read as-is, every booking would shift by the person's offset: four hours
in Montréal in summer, which turns a Saturday evening into a Sunday morning.
The controller converts from the user's timezone, and falls back to UTC rather
than guessing.

**Double booking is closed with a row lock, not with hope.** A Python overlap
check alone leaves a race: two concurrent transactions each read a free slot
and both write, and Odoo runs in `REPEATABLE READ` so neither sees the other's
insert. The guard takes `SELECT ... FOR UPDATE` on the common area first, which
serialises bookings of one space and leaves other spaces alone. A *requested*
slot holds the space just as a confirmed one does, otherwise two people would
be waiting on the same Saturday.

## Parcels and visitors, and the date they expire

The most-loved feature of the competing products, and the most sensitive of the
four. A parcel log says who receives what. A visitor log says who receives
**whom**, and it holds information about **third parties who consented to
nothing at all**: a visitor is neither co-owner nor occupant nor member of
anything.

**Art. 1070 para. 1 CCQ** puts the name and address of every co-owner and every
occupant in the register, and other personal information only with the express
consent of the person concerned. Three design decisions follow, and they are
the feature:

- **Everyone sees only their own.** Their parcels, their visitors. There is no
  "who came through the building today" view on the portal at all.
- **The log expires.** The syndicate sets a retention period and a daily job
  **deletes** what has passed it. It deletes rather than archives: archiving
  keeps the data while hiding it, which is the opposite of the point. Only
  *closed* entries go; a parcel still held and a visitor still expected stay,
  whatever their date.
- **Nothing is kept beyond what serves.** The visitor's name is enough; the
  plate is asked for only where the syndicate runs visitor parking, and the
  field says as much.

Left at zero, the retention deletes nothing, and the module presents that as a
choice rather than as a default.

**No article is cited for the retention.** The project's sourced rulebook
carries the Civil Code and its regulations, not the privacy statute. The module
implements the period and the purge because it is the right way to build this,
without claiming a provision it has not read. That is a question for legal
review, alongside access to a private portion.

**The module claims nothing about custody of a parcel.** It records that one
arrived and that it was handed over. Who answers for it if it disappears is not
something that reads out of the co-ownership title.

**No access hardware.** No lock, no intercom, no door release. Announcing a
visitor warns the caretaker; it opens nothing.

## What an occupant may write, and what only the syndicate may

**Guarding a transition does not guard the field it writes.** A security audit
found that `action_confirm` carried the authority guard while a resident could
simply write `state = "confirmed"` over RPC, self-approving a booking and
voiding the "syndicate confirmation required" setting. The visitor register
went the same way: the host could mark their visitor arrived, backdate the
arrival time, and rewrite the visitor's name after the fact. `date_arrived` is
declared `readonly=True`, which is true of the screen and false of the model.

Both models now declare, on the core authority base, the exact set of fields an
occupant may write and the exact state transitions they may perform themselves
(cancel a booking or a visit; move a confirmed booking back to requested).
Every decision of the syndicate (take a request in charge, settle or refuse
it, confirm or refuse a booking, record a parcel handed over or a visitor
arrived) is refused to anyone outside the manager group. Two rules
make the list hold:

- **Allowlist, not blocklist.** A field added tomorrow is refused by default
  rather than opened by omission.
- **No context flag.** The tempting shortcut is to mark writes coming from the
  model's own methods and let those through. The context is a call parameter:
  the caller builds it. What bounds this is the set of permitted *values*,
  which nobody can forge.

**Moving a confirmed slot is requalified, not refused.** An occupant with a
good reason to change the hour gets to; the booking simply returns to
"requested", because the syndicate's agreement was about an hour it would
otherwise never have seen. Refusing outright would push people to cancel and
re-book, losing the slot in between.

**A visit under way is closed to its host.** Announcing can be corrected;
observing cannot. What the concierge records is the register art. 1070 CCQ
requires the syndicate to keep faithfully, and it is the document that gets
read after an incident.

## Emails

Every message this module sends (the invitation, the staff alert, office
replies and decisions on a request, a booking or a visit) uses the branded
layout of the Symbifox branding module when that module is installed, and
Odoo's light notification layout otherwise. The branding module is not a
dependency. The invitation and the staff alert take their button colour from
the company's brand colour when the company carries one.

## Pages

| Route | What it shows |
|---|---|
| `/my/property` | The person's fractions per syndicate, the capacity in which they hold each, and how to reach the syndicate |
| `/my/property/announcements` | Announcements published, in window, and addressed to them |
| `/my/property/documents` | Documents whose audience covers them, with the register notice when one applies |
| `/my/property/document/<id>` | The file itself, after the record rules have answered |
| `/my/property/requests` | Their own requests, and the form to file a new one |
| `/my/property/requests/new` | The submission itself (POST) |
| `/my/property/bookings` | Bookable spaces, what is taken, and their own bookings |
| `/my/property/bookings/new` | The booking itself (POST) |
| `/my/property/bookings/<id>/cancel` | Cancelling their own (POST) |
| `/my/property/deliveries` | Their parcels, and the visitors they announce |
| `/my/property/visitors/new` | Announcing one (POST) |
| `/my/property/visitors/<id>/cancel` | Cancelling their own (POST) |

## Security

- **Portal users**: read access to announcements, documents, parcels, and to
  the fractions, buildings, syndicates, ownership entries and common spaces
  they are linked to; create on requests; create and write on bookings and
  visits, bounded by the allowlist above. Record rules restrict each model to
  the person's own records or to the audience that covers them.
- **Property users**: read access to everything this module adds.
- **Property managers**: full access, the "Invite to portal" button, and every
  decision of the syndicate.
- Every model also carries a multi-company rule.

## Dependencies

`bf_property_core`, `portal`. Not `bf_property_finance`: contributions and
arrears are a separate step, and a syndicate should be able to open a notice
board without opening its books.

## Tested

The co-owner / occupant partition on both the ORM and the HTTP route, the
refusal to publish a register item unacknowledged, the recategorisation that
unpublishes, the visibility window including the search on the unstored
computed field, roles counted syndicate by syndicate, the pages actually
rendering, the three regimes of art. 1064 CCQ on a request, the create guard
against a neighbour's fraction, the thread that will not close on nothing, and a request filed end to end through the
portal form, CSRF token included; the booking guards (overlap, including the
regression where it had disarmed itself for a portal user, the restricted-use
refusal under art. 1043 CCQ, the caps, and an availability view that yields
slots and never a name); for the logs, the partition per recipient, the
authority guards on every transition, and a purge that removes closed entries
past the retention while never touching what is still open; the office notified
of a filing and the email preference; the decision reaching the requester; the
invitation (the syndicate's, re-sent rather than duplicated, never converting
an internal account, refused to a resident, with a link that opens the
password page, followed over HTTP) and the syndicate's welcome after signup;
and the branded layout on every message.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations, which include administering immovables that you own or
  that you are constituted to administer, and letting a person acting on your
  behalf use your instance for that purpose. A syndicate, a housing cooperative
  or a non-profit housing organisation running the module for its own immovable
  is covered, and so is the bookkeeper or the manager it hires who works inside
  its instance.
- **Requires a written agreement**: administering immovables for the account of
  others, and providing the module as a product or service to third parties,
  whether hosted, managed or resold.
- **Change Date**: on 2030-08-22, this version converts automatically to
  **LGPL-3.0-or-later**.

## Acknowledgements

Created and maintained by Les services de consultation Blue Fox, Inc. AI coding
assistants were used as productivity tools during development.
