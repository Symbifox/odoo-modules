# Changelog

Notable changes to `bf_multi_company_email`.
The format is inspired by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow `18.0.MAJOR.MINOR.PATCH`.

## [18.0.5.2.0] — no merged cells in the signature templates

### Fixed

**Buttons squeezed under the logo.** Odoo 18.0.20261112 adds a normaliser to the
HTML editor, `populateTableSpans`, which removes every `colspan` and `rowspan`
from a table opened in a composer and pads each row with empty cells up to the
widest one. The card spread its rule, buttons, tagline and notice over its
three columns with `colspan="3"`: in the email that left, they fell into the
120 px logo column, stacked. The stored signature was intact and the server saw
nothing; the browser broke the signature while the message was being written.

The card is now a 520 px wrapper table with one column and two rows: the
logo | bar | text row in an inner table, and the rule, buttons, tagline and
notice in a second inner table, one cell per row. A wrapper rather than two
sibling tables, because Word's engine is known to merge or space two adjacent
tables and ignores `max-width` (checked in Chrome; not measured in desktop
Outlook). The banner and two-column templates move their notice into a table of
its own; the compact and minimal templates, one column wide, lose a
`colspan="3"` that earned them two empty cells per row.

**The French copy of the templates kept the old body.** `body_html` is
translatable and every language holds a full copy of it: an upgrade writes only
`en_US`, and `i18n/fr_CA.po` loaded the old body back into `fr_CA`. Any
rendering started from a French interface would have brought it back. The
catalogue now ships these bodies with an empty `msgstr` (there is no text to
translate, so French falls back to the source), and the upgrade removes the
stale copy.

The `end` migration targets signatures by their content (`signature` and the
per-company rendering), renders one person and company at a time while
honouring each company's propagation setting, writes only what changes, and
logs what it cannot repair: a custom template that still renders a `colspan`, a
company notice holding its own merged table, a sending identity carrying its
own copy. Five new tests (`tests/test_sans_colspan.py`), including a port of
Odoo's algorithm that must change nothing in the six rendered templates, in
English and in French, nor in the stored signatures.

## [18.0.5.1.2] — wizards belong to their author

### Security

**A colleague could read another user's wizard.** A transient model is a real
table, and Odoo 18 adds no implicit "creator only" restriction: with an access
right open to every internal user and no record rule, the template gallery and
its lines were readable by any colleague with a plain search. Both now carry a
global rule, `[('create_uid', '=', user.id)]`, on the four operations;
`tests/test_wizard_owner.py` checks that every wizard of the module is covered.

## [18.0.5.1.1] — labels written in English

### Changed

**Source language.** Labels, messages and views are written in English in the
source; the French ships in `i18n/fr_CA.po`. Before this version they read in
French for every user, including users set to English. The upgrade renames the
scheduled job to its English name and keeps the French in the catalogue; it
writes the French only on a database where French is installed.

## [18.0.5.1.0] — quick links render as buttons

### Fixed

**Quick links are buttons.** They were 10 px text in `#999999`: 2.85:1 of
contrast on white where WCAG AA asks for 4.5:1, and the smallest text on the
card. A user reported them as "very small" in Outlook — Outlook renders HTML
with Word's engine, which works in points and compared that 7.5 pt to the body
of the message, Calibri 11 pt by default. Each link is now a table cell with a
filled background and a white **9 pt bold** label, 8×12 px of padding: 13.3:1
of contrast. Three templates follow — card, banner and two columns.

The background rests on `background-color`, and on that alone. `bgcolor` and
`mso-line-height-rule` are there as well, but they are not a belt: measured by
passing the rendering through `html_sanitize` with the exact options of each
field and reading it back in SQL, `bgcolor` survives neither in
`res.users.signature` nor in `mail.message.body`, and `mso-line-height-rule`
survives in the first but not in the second (`sanitize_style=True`). Both pass
intact only through `bf_signature_rendered`, declared `sanitize=False`, which is
the field outgoing notifications read. What survives everywhere, and therefore
carries the button by itself: `background-color`, `padding`, `align`,
`font-size: 9pt`, `font-weight` and `<font>`.

**Pronouns sat at the very figure this version condemns.** Rendered in `#999`
at 10 to 11 px — 2.85:1, two lines above the row of links on the same card.
They now read the company's muted colour (`#666666` by default, 5.74:1) and sit
at **9 pt**, the same floor as the buttons. Five templates follow: card, plain,
compact, banner and two columns. No hardcoded value and no new field: the
template reads a key the rendering already exposes.

- **The emoji is no longer rendered.** Measured against the font metrics: three
  9 pt buttons with an emoji are 533 px wide where the card is 520 px. The
  field stays, for a template of your own; its help says so.
- **The gradient rule has a fallback.** Word's engine draws no
  `linear-gradient`, so under Outlook the rule above the links simply did not
  exist. The solid colour is now declared first, and the rule's `<br/>` no
  longer swells it to the height of a line of text.

### Known limitation

**The button label is hardcoded white, and its background is the company's
"Couleur du texte" field.** A company that picked a light colour there would
make those labels unreadable — the defect fixed here, exactly inverted. WCAG AA
asks for 4.5:1 against white for normal text; the dark colours in common use
are far above it. The field's help now says so. **Follow-up**: choose the label
from the luminance of the background. That needs one more key in
`res.users.bf_signature_data()`, so Python, so a module upgrade — writing a
template body on its own cannot provide it.

### Migration

**`migrations/18.0.5.1.0/end-migration.py`, new.** Without it, a `-u` to this
version rewrites the template bodies and regenerates **nothing**:
`_load_records` sets `install_mode=True`, and `mail_template.write` skips
`_bf_refresh_signatures` under that key, so stored signatures would keep the
10 px row. The script re-renders with `force=True` — otherwise the comparison
against its own previous rendering would skip everybody — and logs a `warning`
for every company whose signature logo is served by `/web/image/website/`, a
route that returns an SVG.

Three choices in that script, each for a measured reason:

- **`end-` and not `post-`.** A `post-` step runs inside the batch loop of
  `load_module_graph`, that is, before the modules depending on this one are
  loaded. A bridge that replaces a signature field with a `related` is not
  loaded yet at that instant, and the rendering then reads the old stored
  column: measured on a test database, a signature lost its pronouns and shrank
  by 89 characters. An `end-` step runs after `registry.loaded = True` and
  `registry.setup_models()`, on the complete registry.
- **Scope: the five templates whose body changes**, not all six. `minimal`
  renders neither links nor pronouns, so re-rendering it would put a rendering
  risk on people this version does not concern.
- **The script cannot raise.** An `end` step that raises never runs again: the
  module version is already recorded, and a recovery `-u` plays no migration at
  all. The body is therefore wrapped, and any exception produces a `warning`
  naming the exact pass to replay by hand. The log also counts the signatures
  **actually rewritten**, read back from the column after a flush — not the
  number of candidates, which the pass may well skip.

### Tests

**`tests/test_gabarits_livres.py`, new.** The six shipped templates were
already rendered by a test: `test_every_shipped_template_renders` puts them all
through the gallery preview. What nothing measured was the **properties** of the
rendered HTML — text size, contrast, a gradient left without a fallback, a logo
served as SVG. Nine tests now hold a 9 pt floor on every button, the absence of
an emoji, a fallback under every gradient, the absence of what Word ignores
(`flex`, `grid`, `inline-block`, `absolute`) and of the `<style>` Gmail strips,
the absence of any SVG logo, and the button background read back in the
`res.users.signature` **column** rather than in the rendering. Geometry is held
by no test: the card's 520 px are measured by hand, and the file's docstring
says so.

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
