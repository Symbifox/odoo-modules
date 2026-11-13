# Multi-company Email (`bf_multi_company_email`)

Template-driven email signatures, plus a sender address per company.

This module is a clean, self-contained replacement for the third-party
`ous_multi_company_email` module. It keeps the three features that module
offered (signature, sender address and catchall/bounce settings per company)
and drops its two destructive behaviours: it never rewrites the partner's email
address, and it never changes the user's default company when they switch
companies in the session.

The user interface of this module is in French. Labels below are quoted as they
appear on screen.

## Signatures rendered from a template

Each company points to a signature template (`res.company.bf_signature_template_id`,
a `mail.template` on the `res.users` model). Every internal user's `signature`
is rendered from it, so the whole company signs the same way.

A template reads a single call, `object.bf_signature_data()`, which returns
plain data: name, pronouns, title, email, phones, company name, website, logo,
colours, font, tagline, confidentiality notice and quick links. Each personal
element is resolved in this order: the override on the user, then the employee
record, then the partner. A person with no HR record still gets a complete
signature.

| What | Where |
|---|---|
| Logo URL and width, accent, text and muted colours, font, toll-free number, tagline | `res.company.bf_signature_*` |
| Confidentiality notice and its size | `res.company.bf_signature_disclaimer`, `bf_signature_disclaimer_size` |
| Pronouns, displayed job title, personal template | `res.users.bf_signature_*` |
| Footer quick links | `bf.signature.link`, owned by a company **or** a person, never both |

Phone links are written in E.164 (`tel:+15555550142`) while the displayed number
keeps its readable form, and the logo link is forced to `https://` even when the
company website field says `http://`.

### Six templates, and a gallery to choose one

Two templates ship as defaults, a card and a plain text block, and four more
come in the gallery: compact (one line), banner (coloured bar on the left),
minimal, and two columns. None of them names an organisation.

The gallery (**Choisir un modèle de signature**) renders every template with the
real data of the person looking at it, side by side, and assigns nothing until
they pick one (**Utiliser ce modèle**). A person only ever changes their own
signature, and cannot pick one while their company enforces its signature. An
administrator can apply the choice to the whole company. **Revenir au modèle de
la société** drops a personal choice.

Only templates marked **Modèle de signature** (`mail.template.bf_is_signature`)
and written on `res.users` appear in the gallery and in the signature
selectors. The marker is explicit because Odoo ships its own templates on
`res.users` (two-factor invitation, portal account creation), which must never
become someone's signature. Only administrators (`base.group_system`) create,
mark or edit a signature template: it is rendered with superuser rights and
ends up in a whole company's signature.

### What a mail client does with it

There is no browser at the end of an email. Outlook renders HTML with Word's
engine, which ignores `max-width`, `display: inline-block`, `border-radius` and
`linear-gradient`, and which works in **points** rather than pixels. The shipped
templates are built for that engine, and `tests/test_gabarits_livres.py`
measures the properties of what they render rather than only that they render:

- **Quick links are filled buttons** in the card, the banner and the two
  columns — a table cell with a background colour and a white 9 pt bold label,
  13.3:1 of contrast. Up to 18.0.5.0.3 they were 10 px grey text at 2.85:1,
  which Word compared to an 11 pt message body and showed very small.
- **Nothing smaller than 9 pt**, on a button or on the pronouns beside a name.
- **Every gradient has a solid fallback declared before it**, because Word draws
  none.
- **No SVG logo.** Neither Gmail nor Outlook draws an SVG inside an `<img>`; a
  `bf_signature_logo_url` served by `/web/image/website/` returns one, and the
  upgrade logs a warning naming each company that still points there.

One limitation comes with the buttons: their label is hardcoded white and their
background is the company's **Couleur du texte**. A light colour there makes the
labels unreadable. The field's help says so; choosing the label from the
luminance of the background is a follow-up.

### Signatures stay current

Signatures are **stored**, because `res.users.signature` is the field Odoo, the
composer and third-party modules read. The module re-renders them when their
inputs change:

- saving the body of a template used as a signature;
- writing a company brand element (name, website, email, logo, colours, font,
  toll-free number, tagline, notice);
- writing a person's own inputs (company, name, email, pronouns, displayed
  title, personal template, use of company links);
- adding, changing or removing a quick link.

`bf_signature_auto_apply` turns this off per company. The manual paths remain,
for administrators: **Appliquer à tout le monde** on the company form and in
Settings, and the **Régénérer les signatures courriel** server action on the
Users list.

Re-rendering is idempotent: it compares against its own stored rendering, never
against `signature`, which is sanitised on write and would never compare equal.

### Enforcing the company signature

`bf_signature_enforce` locks the signature field in the user preferences and
replaces any hand-written value with the rendered one, on the server. The test
is "is this person locked", never "does the submitted HTML differ": the HTML
editor reformats what it loads, so a byte comparison would reject ordinary
saves. Members of `base.group_system` are exempt.

### More than one company

`res.users.signature` holds a single value, so on a multi-company database it
can only carry one company's signature. `bf_signature_rendered` (company
dependent) keeps one rendering per company, and outgoing notifications use the
rendering of the company the message is sent for, the same company the email
layout already resolved. `signature` keeps the home company's rendering, which
is what the composer previews.

### Confidentiality notice

`bf_signature_disclaimer` is an optional, translatable legal block rendered at
the bottom of the signature, in very small, small or normal size. It travels
with the signature: replies sent without a signature do not carry it. A notice
added to all outgoing mail belongs on the mail gateway, not here.

## Quick links

A quick link (`bf.signature.link`) is a short label and an address, shown in
the signature footer as a filled button. The `icon` field still holds one
emoji per link, but no shipped template renders it since 18.0.5.1.0: three
9 pt buttons with an emoji are 533 px wide where the card is 520 px. A
template of your own can still read it. Personal links come first, then the
company's, unless the person unticks **Reprendre les liens de la société**. A
booking link that points at one person's calendar belongs on that person: on
the company it would send every prospect of the team to the same calendar.

Addresses must start with `https://`, `http://`, `mailto:` or `tel:`. A link
belongs to exactly one owner, enforced by a database constraint.

### Tracked shortlinks

With **Passer par un raccourci traçable**, the link is published as `/r/<code>`
(Odoo's `link_tracker`). A signature lives for years in other people's
mailboxes; with a shortlink, changing the destination is one write on the
server and every signature already pasted elsewhere follows. The click count
comes with it.

A link only takes over an existing shortlink that already belongs to its
owner. A code typed by hand that is already used elsewhere is refused, and a
suggested code that is taken gets a free numbered suffix. Only an
administrator can attach a shortlink created outside this module, by typing
its code.

### Links that provision themselves

A link can also follow a source instead of a typed address:

- **Ma page de rendez-vous**: the person's public booking page, when every
  resource behind a public booking type is that person (`resource_booking` with
  `bf_appointment`). A page shared by several people is skipped.
- **Ma page de dépôt sécurisée**: the person's secure drop page, as published by
  `bf_securetransfer`. This module reads it and never creates it.

Neither module is a dependency: their presence is tested when a link is
resolved. **Provisionner les liens rapides** (Action menu of the quick links
list, administrators only) creates the missing links for every active internal
user, and a scheduled action (inactive by default) keeps them
pointing at the right page. A source that resolves to nothing creates no link,
and a link already typed by hand for the same destination is never duplicated.
Service accounts are skipped.

## Sender address per company

`res.users.bf_company_email` (company dependent) sets the `email_from` of the
messages a person posts while that company is active. It is applied in
`mail.thread.message_post`, only to messages that person authors, and the
person stays the author; the partner record is never touched. Odoo may still
rewrite the `From` header at sending time to match the company's alias domain.

## Legacy catchall, bounce and default sender parameters

`res.company.bf_catchall_domain`, `bf_catchall_alias`, `bf_bounce_alias` and
`bf_default_from` override the matching `mail.*` system parameters
(`mail.catchall.domain`, `mail.catchall.alias`, `mail.bounce.alias`,
`mail.default.from`) when their company is the active one.

This only matters for modules that still read those parameters. Odoo 18 itself
routes mail through `mail.alias.domain`, one per company, and reads these
parameters only once, when `mail` is installed: to route a company's mail, set
its alias domain in Odoo.

## Where it lives in the interface

Signatures are configuration, not a workspace: no application tile, no root
menu.

- **Courriels > Configuration**: **Ma signature courriel**, **Liens rapides de
  signature**, and **Gabarits de signature** (administrators).
- **Paramètres > Général**, in the Emails block: the company template, the
  enforcement and propagation switches, the brand elements, the notice, and the
  **Appliquer à tout le monde** and **Liens rapides** buttons. The block follows
  the company selector at the top of the screen.
- The company form and the user form each get a **Courriels multi-sociétés**
  tab.

## Installation

On install, the module assigns the card template to every company that has
none, then renders every internal user's signature.

Upgrading to 18.0.5.1.0 runs `migrations/18.0.5.1.0/end-migration.py`, which
re-renders the stored signatures of the five templates whose body that version
changes. It is needed because loading a data file sets `install_mode=True`, and
under that key writing a template body does not trigger the usual re-render:
without the script the bodies would change while the signatures already stored
kept the old row. The step runs at `end`, once the registry is complete, so a
bridge that replaces a signature field is loaded before anything is rendered.
It never raises; on any error it logs the exact pass to replay by hand.

Brand elements start neutral (a blue accent, a system font stack). An
installation can seed its own values from an optional `data/brand_seed.private.json`
file, which is not part of this repository: keys `companies` (by company name)
and `users` (by login), each with `fields` and `links`. A seed only replaces a
value still equal to the field default, so a value chosen by hand is never
overwritten. Without the file, the module logs that it found no seed and keeps
its defaults.

### Moving from `ous_multi_company_email`

The install hook copies, once and idempotently, the existing `ous` settings
into this module's fields (catchall fields, per-company sender address). Install
this module first, check the signatures and the sender addresses, then uninstall
`ous_multi_company_email`.

## Requirements

- Odoo 18.0: `base`, `mail`, `hr`, `link_tracker`.
- `bf_email_management` (Symbifox), for the **Courriels > Configuration** menu.
- Optional: `resource_booking` with `bf_appointment` (booking page links),
  `bf_securetransfer` (drop page links).

The bridge `bf_preferred_name_signature` (installed automatically with
`bf_preferred_name`) makes the signature pronouns those of the person's
contact.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## Licence

LGPL-3. See [LICENSE](LICENSE).
