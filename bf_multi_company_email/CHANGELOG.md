# Changelog

Notable changes to `bf_multi_company_email`.
The format is inspired by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow `18.0.MAJOR.MINOR.PATCH`.

## [18.0.5.0.3] — first public release

The module had run in production for several months before this release. This
entry describes what it does at publication.

### Security (fixed before publication)

A review before publication found that an ordinary internal user could, through
RPC, change what was not theirs. All of it is refused now, each refusal with its
test (`tests/test_acces.py`):

- creating a link with the code of an existing `/r/` shortlink took that
  shortlink over and changed its destination with superuser rights, whoever it
  belonged to; the code is now checked as soon as it is typed, link fields
  that only the module writes (`tracker_id`, `auto_managed`, `url`) cannot be
  set through RPC or context defaults, and provisioning, which runs with
  superuser rights, only touches the links it created and never takes over a
  shortlink made by hand;
- the quick link commands written from one's own user record ran with superuser
  rights, so they could edit, delete or take over other people's and the
  company's links;
- the gallery trusted its `user_id` and `can_set_company` values, so it could
  change a colleague's signature or a whole company's template, and choosing
  another company in it lifted an enforced signature;
- any internal user could create or mark a signature template, run the
  provisioning of everybody's links, or re-render a whole company;
- a context key lifted the enforced signature.

A test of real sending before publication also found that a message posted
with the company sender address lost its author (Odoo looks the author up by
address, and that address is on no contact), so it went out without a
signature. The author is now kept, and the company address no longer applies to
a message authored by someone else.

### Signatures

- One signature template per company, rendered for every internal user from a
  single data call, `object.bf_signature_data()`. Personal elements resolve from
  the user override, then the employee record, then the partner.
- Brand elements are fields on the company (logo, colours, font, toll-free
  number, tagline) and on the user (pronouns, displayed job title, personal
  template).
- Six templates: card and plain text by default, plus compact, banner, minimal
  and two columns in the gallery. A gallery previews each template with the
  viewer's own data before anything is assigned.
- Templates are marked explicitly as signature templates
  (`mail.template.bf_is_signature`), so Odoo's own `res.users` templates never
  show up as signatures.
- Signatures re-render when a template, a company brand element, a person's
  data or a quick link changes; propagation can be turned off per company.
- A company can enforce its signature; administrators stay free.
- One rendering per company for multi-company users, used by outgoing
  notifications.
- Optional confidentiality notice, translatable, in three sizes.
- Phone links in E.164, logo link forced to `https://`.

### Quick links

- Footer links owned by a company or by a person (database constraint), personal
  links first.
- Optional tracked shortlinks (`/r/<code>`) through `link_tracker`, so a
  destination can change without anyone pasting their signature again.
- Links that provision themselves from the person's booking page or secure drop
  page, when those modules are installed, without depending on them.

### Mail

- Sender address per user and per company, applied at post time without
  touching the partner.
- Per-company values for the legacy `mail.catchall.*`, `mail.bounce.alias` and
  `mail.default.from` parameters, for modules that still read them.
- One-time import of the settings of `ous_multi_company_email` at install.
