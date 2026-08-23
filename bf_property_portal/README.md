# Co-ownership — Occupant portal (`bf_property_portal`)

What the syndicat makes available to the people in the building, and to whom.
Announcements, documents, the contact details of the syndicat itself, and
maintenance requests, on the standard Odoo portal.

## Why it is not a generic notice board

Two provisions decide almost every design choice here.

**Article 1070 C.c.Q.** lists what the syndicat keeps in its register: minutes
of the general meeting **and of the board**, written resolutions, the by-laws
of the immovable, the financial statements, the declaration of co-ownership,
contracts, the cadastral plan, plans and specifications, location certificates,
the maintenance log, the contingency fund study, and the description of the
private portions. The same article puts the name and address of every co-owner
and of every tenant in the register, and puts **other** personal information
there only with the express consent of the person concerned.

**Article 1070.1** says how the register is consulted: in the presence of a
director or of a person designated by the board, at reasonable hours, and
according to the by-laws of the immovable. A copy is obtained by a co-owner
**for a reasonable fee**.

Three conditions, then, and a fee. Putting a register item on a portal that is
open at all hours gives **more** than the article requires. That is not
unlawful, and a syndicat may well want it. It is a decision, and this module
makes the syndicat take it rather than taking it by default.

## What that produces

- **A document carries its regime, through its category.** Thirteen of the
  categories are register items under article 1070; the rest (notices, guides,
  forms) the syndicat disposes of freely. The flag is computed, not typed.
- **Publishing a register item is refused until it is acknowledged.** The
  refusal quotes article 1070.1, and the publication is recorded in the log
  with the audience it was given.
- **Changing the category of a published document unpublishes it** when it
  becomes a register item that nobody acknowledged. Without that, a notice
  turned into minutes would stay online with no decision behind it.
- **A reader who is shown a register item is told so**, and told what article
  1070.1 would otherwise require.

## The tenant is not the co-owner

A tenant is *in* the register (article 1070 al. 1, name and address). That
gives no right *to* the register, which article 1070.1 reserves to the
co-owner. So every announcement and every document carries an audience:
co-owners, occupants, or both.

⚠️ **The partition is enforced by record rules, not by the views.** An
`invisible` attribute hides a field on a screen; it protects neither an ORM
read nor the route that serves a file. A tenant who guesses a document id gets
nothing, and a test proves it over HTTP rather than through the ORM alone.

⚠️ **No anonymous links.** No `portal.mixin`, no access token. A register item
served by a URL that can be forwarded would hand to anyone what article 1070.1
gives to a co-owner under conditions. A session is always required.

⚠️ **No directory of co-owners.** The portal shows how to reach the syndicat,
and nothing about the other occupants.

## A rule domain carries no date

The visibility window of an announcement (published, started, not yet expired)
is applied by the **controller**, on every request, and never by a record rule.

An `ir.rule` domain is cached by `ormcache` on `(uid, su, model, mode,
companies)` with **no time component**: a date written into `domain_force` is
evaluated once and then frozen until the cache is invalidated. The same trap
has already been paid for elsewhere in this repository, where an attachment
stayed readable past its own expiry date. The rules here answer only a question
that does not depend on the hour: does this person have a current link to this
syndicat, and in what capacity.

## Access granted to a portal user

Read only, and bounded by its own rules: the fractions they own or occupy, the
buildings and syndicats that carry them, their own entry in the ownership
register, and the announcements and documents whose audience covers them.

⚠️ These reads are not decoration. A rule that **traverses**
(`syndicat_id.unit_ids.ownership_ids`) is evaluated with the reader's own
rights, so reading an announcement requires being able to read the syndicat,
the fraction and the register entry crossed on the way. Without them the page
answers 403, which is exactly how the HTTP test found the omission.

## Maintenance requests

An occupant reports something; the syndicat answers on the same thread. Not to
be confused with the **maintenance log** of article 1070.2 and its regulation,
which is a statutory document established by an independent professional. This
is a ticket about a garage door that squeaks.

**Who bears the cost is computed, and only from what the text says.** Article
1064 again, and its three regimes: a general common portion falls on every
fraction in proportion to its relative value; current maintenance and repairs of
a restricted-use common portion fall on the co-owners who have the use of it
alone; **major repairs and replacement of that same restricted-use portion
follow the general rule** and fall on every fraction, absent a clause in the
declaration. The module displays that reading with its article. It invoices
nothing: allocation lives in the budget.

**A request about a private portion is not silently accepted as the syndicat's
business.** Article 1039 gives the syndicat its object: the conservation of the
immovable, the maintenance and administration of the common portions, the
safeguard of the rights attached to the immovable, and operations of common
interest. A request about a private portion is shown against that object rather
than being quietly costed to everyone.

⚠️ **What is deliberately not encoded.** Access to a private portion to carry
out works, the notice that must precede it, and the indemnity for any damage:
none of those provisions are carried by the project's sourced rulebook, and
nothing here is written from a recollection of their text. The module records
that a request concerns a private portion and asserts nothing about the access
regime. It is a question for the legal review.

⚠️ **The response deadline has no statutory source.** No provision obliges a
syndicat to answer an occupant within a number of days. The module claims no
legal deadline: the syndicat enters the commitment it gives itself, and the
module counts the days of that commitment. Left at zero, nothing is counted and
no lateness is shown.

**A thread does not close on nothing.** Closing a request, or refusing it as
outside the syndicat's object, requires saying what was done or why. That is
what somebody will read in two years looking for when the leak was repaired.

### What a portal user may do

Create a request, and read their own. ⚠️ **Not the neighbours'.** A ticket often
describes somebody's problem, with their door number and their own words;
article 1070 al. 1 puts a third party's personal information in the register
only with their express consent, and publishing everyone's threads would do the
opposite. The syndicat sees all of them from the back office.

🔴 **A `unit_id` posted by a browser is not proof of a link.** The controller
keeps only the fractions the person actually holds, and `create()` checks the
entitlement again on the server. The create is deliberately **not** run under
`sudo`, because that would make the model-level guard inert and leave the
controller as sole judge. Two things are sudoed, each for a stated reason: the
sequence (a portal user has no read access to `ir.sequence`, and without it the
very first request filed by an occupant fails on an ACL) and the opening chatter
message (a portal user cannot create a `mail.message` on their own authority) —
whose author stays the requester, so the thread carries their name.

## Pages

| Route | What it shows |
|---|---|
| `/my/property` | The person's fractions per syndicat, the capacity in which they hold each, and how to reach the syndicat |
| `/my/property/announcements` | Announcements published, in window, and addressed to them |
| `/my/property/documents` | Documents whose audience covers them, with the register notice when one applies |
| `/my/property/document/<id>` | The file itself, after the record rules have answered |
| `/my/property/requests` | Their own requests, and the form to file a new one |
| `/my/property/requests/new` | The submission itself (POST) |

## Dependencies

`bf_property_core`, `portal`. Not `bf_property_finance`: contributions and
arrears are a separate step, and a syndicat should be able to open a notice
board without opening its books.

## Tested

55 tests on staging: the co-owner / tenant partition on both the ORM and the
HTTP route, the refusal to publish a register item unacknowledged, the
recategorisation that unpublishes, the visibility window including the search
on the unstored computed field, roles counted syndicat by syndicat, the pages
actually rendering, the three regimes of article 1064 on a request, the create
guard against a neighbour's fraction, the thread that will not close on
nothing, and a request filed end to end through the portal form, CSRF token
included.

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
