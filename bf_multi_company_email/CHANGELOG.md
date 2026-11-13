# Changelog

Notable changes to `bf_multi_company_email`.
The format is inspired by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow `18.0.MAJOR.MINOR.PATCH`.

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
