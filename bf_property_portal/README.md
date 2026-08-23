# Co-ownership — Occupant portal (`bf_property_portal`)

What the syndicat makes available to the people in the building, and to whom.
Announcements, documents, and the contact details of the syndicat itself, on
the standard Odoo portal.

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

## Pages

| Route | What it shows |
|---|---|
| `/my/property` | The person's fractions per syndicat, the capacity in which they hold each, and how to reach the syndicat |
| `/my/property/announcements` | Announcements published, in window, and addressed to them |
| `/my/property/documents` | Documents whose audience covers them, with the register notice when one applies |
| `/my/property/document/<id>` | The file itself, after the record rules have answered |

## Dependencies

`bf_property_core`, `portal`. Not `bf_property_finance`: contributions and
arrears are a separate step, and a syndicat should be able to open a notice
board without opening its books.

## Tested

29 tests on staging: the co-owner / tenant partition on both the ORM and the
HTTP route, the refusal to publish a register item unacknowledged, the
recategorisation that unpublishes, the visibility window including the search
on the unstored computed field, roles counted syndicat by syndicat, and the
three pages actually rendering.

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
