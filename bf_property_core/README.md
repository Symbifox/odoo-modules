# Buildings: Foundation (`bf_property_core`)

The neutral foundation of the property suite for Odoo 18 Community:
organisations, buildings, dwellings and the people who own or occupy them. It
serves Quebec divided co-ownership (syndicates of co-owners) and multi-residential
rental alike; everything specific to one regime lives in the modules that depend
on this one.

## What it does

| Model | Purpose |
|---|---|
| `bf.property.organisation` | The organisation responsible for buildings, with a `kind`: syndicate of co-owners, landlord, housing cooperative or housing non-profit. For a syndicate it also carries the date of the declaration of co-ownership, the enterprise number and the quote-part base |
| `bf.property.building` | A building of the organisation, with address, cadastral lot, year built and number of storeys |
| `bf.property.unit` | A fraction or dwelling (private portion) with its quote-part, type (residential, parking, storage, commercial, other), floor and area |
| `bf.property.ownership` | Ownership history per fraction, including undivided co-ownership with each holder's share |
| `bf.property.common.area` | Common portions, general or restricted-use (art. 1043 CCQ), optionally marked as bookable by occupants |

A fraction can be marked as rented and carry an occupant distinct from its
owner. The fraction number is unique per building among live fractions only, so
a keying mistake can be corrected by archiving the record and entering the same
number again.

### Shared building blocks for the rest of the suite

- **Authority guard** (`bf.property.organisation.authority`, abstract). One
  place that answers "does this belong to the organisation or to the occupant?".
  `_ensure_organisation_decides()` raises unless the caller is a property
  manager, and is meant to be called *before* any effect: a text message, an
  email or a file handed to a third party cannot be recalled by a rollback.
  `_ensure_portal_write_scope()` limits what an occupant may write on their own
  record to an allow-list of fields and of state transitions; a field added
  later is refused by default rather than opened by oversight. Its twin,
  `_ensure_portal_create_scope()`, holds the other door: a portal user can
  create a booking, a visitor or a request, but only with the fields of a
  submission, so the state, the observed times and the resolution are born from
  their defaults and never from the caller. It reads the context too: a
  `default_<field>` key for a field outside the submission is refused, since
  Odoo fills missing values from the context after the guard has run. There is
  no context flag to bypass either, because a context is something the caller
  builds.
- **Notices that actually leave** (`_bf_notify_partners`, same abstract model).
  Posts to the record's thread *and* emails the named people. It returns who
  was reached and who was not (no email address), so the calling module can say
  on the thread that the organisation must notify the others some other way. A
  note on a thread notifies nobody outside the office.
- **Syndicate regime** (`bf.property.syndicat.regime`, abstract). Objects that
  only exist in divided co-ownership (general meeting, contingency fund,
  common-expense statement, the certificate of art. 1068.1 CCQ) inherit it and
  cannot be attached to an organisation that is not a syndicate. It raises
  `ValidationError`, not `AccessError`: the manager does have the right, but the
  object does not exist in that regime.
- **Portal access given by the office**
  (`res.partner._bf_property_grant_portal(template_xmlid, organisation)`).
  Invites a person to the portal or re-sends their access, and returns who was
  invited, who was re-sent access and who was skipped. It is reserved to
  property managers. People without an email address, and people who already
  hold an *internal* account, are skipped, never converted: the portal is not a
  door to the back office. A withdrawn portal account is reactivated. The
  email that goes out is the organisation's own template passed by the caller
  (with a link that lets the person choose a password), not Odoo's generic
  portal invitation. The person is marked with `bf_property_invited_by_id`.
  This method needs the `portal` and `auth_signup` modules at run time; only the
  modules that depend on them (occupant portal, tenant portal) call it.
- **Welcome after sign-up on behalf of the organisation.** When a person the
  suite invited completes sign-up, Odoo's generic "account created" email is
  replaced, for that email only, by a welcome in the name of their syndicate or
  landlord (`mail_template_resident_welcome`). Everyone else on the instance
  keeps Odoo's ordinary welcome. The substitution happens only when the email is
  sent as superuser, which is how Odoo's sign-up controller sends it:
  `send_mail` is callable over RPC, and any other caller goes through Odoo's own
  path, access check included.
- **Email layout** (`bf_mail_layout(env)`). Every email of the suite uses the
  Symbifox branding module's layout (`bluefox_branding.bf_mail_layout`) when
  that module is installed, and otherwise Odoo's light layout
  (`mail.mail_notification_light`). The branding module is not a dependency.
- **Numbers in a sentence** (`tools.format_decimal`). Renders a value with at
  most four decimals and no trailing zeros, using the reader's language for
  separators, so a sentence reads "585 votes for out of 502.5 required" rather
  than "585.0000 ... 502.5000".

## Design notes

**Quote-parts are checked, not enforced.** The total of all live fractions is
computed continuously and compared against the syndicate's declared base (1000
for thousandths, 10000 for ten-thousandths). A gap shows as a visible state
(no fraction, incomplete, balanced, over) but never blocks a save. A building
is keyed in fraction by fraction, and a hard constraint would make the first
save impossible.

**Ownership is a history, not a pointer.** Article 1070 CCQ requires the
syndicate to keep a register of co-owners and occupants. Knowing who owns a
fraction *today* is not enough, so ownership is a dated relation with an
overlap-aware constraint: simultaneous shares cannot exceed 100 %, while
consecutive owners at 100 % each are perfectly normal.

**Date-derived fields are refreshed nightly.** `is_current` and a fraction's
current owners are stored so they stay searchable, but they depend on today's
date rather than on a write. A daily cron re-flags the records whose window has
lapsed. Without it, an ownership ending 31 December would keep showing its
former holder as a current co-owner until someone happened to edit the record.

**Every scheduled action of the suite runs at 05:30 UTC.** A scheduled action
has no time zone: its "today" is UTC's, and it used to run at the hour it was
installed. Installed in a Québec evening, it already saw the next day. At 05:30
UTC the date is the same in Québec, summer and winter. The cron files carry
that hour for a new installation; `bf_property_core.tools.anchor_crons_at_dawn`
re-anchors existing crons from each module's migration, since the files are
`noupdate`. ⚠️ This holds for a run on time: a run caught up after an outage
between midnight and 05:00 UTC happens on a Québec evening, and sees the next
day.

**Archiving is honest.** Archiving a fraction removes it from the quote-part
total, which will usually flip the syndicate to *incomplete*. That is intended:
if the live fractions no longer cover the declared base, the data no longer
matches the declaration and the screen should say so.

**Unticking "rented" clears the occupant.** An occupant distinct from the owner
requires the fraction to be marked as rented. Unticking the box empties the
occupant in the same write rather than refusing it.

**No stored sentence.** A test walks the registry of every installed module of
the suite and fails if a stored computed field assembles a translatable
sentence: a stored sentence is frozen in the language of whoever triggered the
computation, and the next reader sees it in that language.

## Legal basis

Every rule encoded in this suite carries its primary source: the consolidated
Civil Code of Québec, the regulations made under it, and the transitional
provisions of the 2019 statute on divided co-ownership (Bill 16). None comes
from secondary commentary. Where the text is silent, the module says so on
screen rather than inventing a threshold.

This is software, not legal advice. An organisation remains responsible for the
decisions it takes.

## Scope

This module is structure only. Governance (general meetings, weighted votes,
minutes), common expenses, the contingency fund and the other co-ownership
obligations (maintenance log, contingency fund study, the syndicate's
certificate) belong to separate modules, as do leases and rental notices.

## Security

| Group | Rights |
|---|---|
| Consultation (`group_bf_property_user`) | Reads organisations, buildings, fractions, ownerships and common areas |
| Manager (`group_bf_property_manager`) | Creates and modifies all of the above; implies Consultation |

Multi-company record rules restrict every model to the companies open to the
user. The ownership rule matters most: it holds co-owners' names, which are
personal information.

## Dependencies

`base`, `mail`. Nothing else. No hard dependency on any other module of this
repository.

## Tested

29 tests: structure and quote-part arithmetic, indivision, ownership history
windows, the guard rails, the refresh cron, archiving behaviour, the
organisation kinds and the syndicate regime guard, the suite-wide guard
against stored sentences, and the suite-wide guard on the hour of the
scheduled actions.

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

## Why this module stopped being about syndicates

The model that owns buildings used to be called `bf.property.syndicat`, and that
was right for as long as the product served divided co-ownership only. Then
multi-residential rental entered the scope, and the occupant portal (notices,
documents, maintenance requests, common-area bookings, parcels, visitors) turned
out to apply identically to a rental building. Seven models, none of which knows
anything about co-ownership.

The alternative was to keep two copies of that portal. But **a guard that gets
copied is a guard that will be missing somewhere**. Two portals means fixing
everything twice, and one day when one of them has not received the fix.

So `bf.property.syndicat` became `bf.property.organisation`, with a `kind`:
syndicate of co-owners, landlord, housing cooperative, housing non-profit. The
field `syndicat_id` became `organisation_id` in all 427 places it appeared,
including the co-ownership modules, because one concept deserves one name and a
mixed convention is how somebody later reaches for the wrong one.

⚠️ **"Organisation", not "manager".** The licence's Additional Use Grant excludes
administering immovables for the account of others. A name suggesting third-party
property management would describe a use the licence does not permit.

⚠️ **Nothing had been published or installed anywhere yet**, so the rename cost
no migration. Once a first installation existed, it would have cost one, and
`ir_model_data` would have had to be reassigned by hand. That timing was the
whole argument for doing it then rather than later.

### What refuses to exist outside the co-ownership regime

An annual general meeting, a contingency fund, a common-expense statement or an
art. 1068.1 CCQ certificate at a landlord's are legal nonsense, and until the
rename nothing prevented creating them. The models concerned now inherit
`bf.property.syndicat.regime`, a single base that refuses when the organisation
is not a syndicate.

### What is still in the wrong place

The quote-part machinery (the base, the total, the balance check) means nothing
at a landlord's, and it still lives in this module. Moving it out is a second
piece of work, to be done when the rental side needs a smaller core. Until then
those fields are hidden outside the syndicate regime rather than removed, which
is honest about the state of things rather than tidy about it.
