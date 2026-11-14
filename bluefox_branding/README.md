# Configurable Branding Pack (`bluefox_branding`)

Per-company brand colors, fonts, logo and branded email layouts for Odoo
Community. Every surface follows the **active / record company**, so a
multi-company database renders each company in its own identity — no SCSS
recompile, no per-tenant build.

The fallback values match Odoo's stock theme, so installing the module is a
visual no-op until brand fields are configured.

## Configuration

Everything lives in one panel: **Settings → General Settings → Identité de
marque**.

- **Couleurs de marque (interface et courriels)** — `report_brand_primary` /
  `report_brand_dark`: navbar, buttons, and branded emails.
- **Logo sur fond foncé** — `report_brand_logo`: a light/white logo used on dark
  header bands (branded emails, public pages). Falls back to the standard logo
  when empty, so light-background documents keep the colored company logo.
- **Couleurs des rapports PDF** — Odoo's native `primary_color` /
  `secondary_color`: PDF report header/accents (surfaced here for convenience;
  they drive the document layout, not the colors above).
- **Identité visuelle** — logo, favicon, website, report header tagline.
- **Typographie** — company font (Lexend by default, via `bf_lexend`).
- **Courriels brandés** — email tagline, custom footer HTML, default
  signature, and optional privacy / terms links.

All fields are stored on `res.company` and read at render time, so switching
the active company re-skins the UI on the next page load and re-brands the
next outgoing email.

## What this module does

### Backend + portal chrome

Navbar, menus, buttons, badges, progress bars, links and kanban accents
re-skin via CSS variables (`var(--brand-primary, …)`, `var(--brand-dark, …)`)
populated at request time from the **company selected in the switcher**, not
`env.company` alone: on a server-rendered page the switcher only writes a `cids`
cookie, so the variables are resolved by `res.company._brand_active_company`
(v18.0.3.8.0). See `static/src/scss/branding.scss`.

### Frame shape per company (v18.0.3.27.0)

Three settings in the Brand Identity panel give a company the shape of its
frames: corners (Square, Soft, Rounded), shadow (None, Subtle, Pronounced) and
border weight (Thin, Strong). They reach the three surfaces that draw a frame,
each by the only mechanism available to it:

- **web client**: Bootstrap's own variables are redefined in the per-company
  `<style>` block, so cards, modals, dropdowns and buttons follow;
- **emails**: literal values are written into the layouts (card, buttons,
  rules), because a mail client cannot resolve a CSS variable;
- **PDF**: the per-company stylesheet Odoo compiles for reports, because
  wkhtmltopdf ignores `var()`.

The defaults reproduce the previous appearance and emit nothing, so a company
that never opens the panel sees no change. The web client only: the website, the
portal and the login page keep their own theme.

### The web client's top bar at phone width (v18.0.3.31.0)

Below 768 px the systray may shrink and scroll horizontally instead of pushing
the bar wider than the viewport. At 390 px the measured overflow was 212 px, and
it always came from `.o_menu_systray`, which Odoo lays out at `flex-shrink: 0`.
Nothing is removed and nothing changes above 768 px. See
`static/src/scss/mobile_shell.scss`.

### An accent that can carry white text (v18.0.3.10.0)

The brand accent is used as a **background** under white text on primary
buttons, badges, progress bars and the Discuss counter. Measured on a live
instance: a light blue accent under white renders **2.62:1**, well under the
4.5 AA threshold, and a light orange one 2.96:1. Nothing reports it. The button
is drawn, the label is there, it is simply too pale for some readers, and the
same pair behaves identically in light and dark mode.

A blanket darkening would be wrong: three of the seven accents in service
already pass (Odoo's own purple renders 7.23:1), and darkening them would only
spoil them. So the variant is computed **per brand**, server side, by
`brand_color_mixin.assombrir_pour_texte_blanc`: the colour is stepped down by
1% at a time and the **lightest** version that reaches the threshold wins, so a
brand stays as close to itself as the threshold allows. One light blue loses
26% of its lightness, another 7%, and a brand that already passes is returned
untouched.

The result is published as a second CSS variable, `--brand-primary-on-white`,
alongside `--brand-primary`. The rule for anything this module styles:
**if white text sits on it, read `-on-white`; otherwise read the raw accent**,
which still paints borders, links, icons and focus rings.

The fallback is chained, `var(--brand-primary-on-white, var(--brand-primary,
…))`, and that is deliberate: the asset bundle is rebuilt as soon as the file
changes on disk, while the variable only appears once the template is upgraded.
A hardcoded fallback would repaint every company in the first one's colour
during that window; chained, the window renders exactly what it rendered
before.

Six tests cover it, proven by mutation: the seven accents in service, the
"lightest that passes" property, the untouched-if-already-legible case,
unreadable input, the company accessor, and the stylesheet itself, because
the original defect was not a wrong colour: it was the right colour read from
the wrong variable.

### Application icon (tab, home screen, installed PWA)

`res.company.favicon` feeds three surfaces at once, so a white-label tenant
stops showing Odoo's purple icon: the backend browser tab
(`x_icon` on `web.webclient_bootstrap`), the iOS `apple-touch-icon`, and the
`icons` of the PWA manifest — main and scoped alike — alongside the
`background_color` / `theme_color` this module already sets
(`controllers/webmanifest.py`). Upload a **square PNG of 512 px**; the URLs are
built by `res.company._brand_icon_url()` and resized by `/web/image`.

One deliberate limit stays: an `.ico` favicon still brands the tab but is
skipped in the manifest, which Chrome cannot decode there. The website and
portal keep `website.favicon`, untouched.

### Per-application icons, and installing any app (v18.0.3.6.0)

Odoo builds every piece of the scoped-app PWA generically — `/scoped_app`
serves any module as its own installable application, with its own scope, name
and icon — and then narrows it twice. This version widens both.

**Icons.** `_get_scoped_app_icons` looks only for
`<module>/static/description/icon.svg`. Not one bf_* module ships an SVG, so
every Symbifox application installed to a home screen fell back to the same
icon and they were indistinguishable. The icon each module *does* ship is a
PNG, unusable as-is: they run small and off-square (140x138, 140x123, 256x256
across the suite) where a manifest icon must be at least 192, and a maskable
one is cropped to a shape inscribed in the square. `controllers/app_icons.py`
re-renders that PNG centred on a square canvas and
`/bluefox_branding/app_icon/<module>/<size>.png` serves it at 192 and 512, in
`any` and `maskable` purposes. A module that really does carry an SVG keeps
Odoo's own handling.

**The menu entry.** Odoo gates "Install <app>" on a hardcoded list of three
actionPaths (`barcode`, `field-service`, `shop-floor`); its own comment says
the feature works for all apps and the list can grow.
`static/src/js/install_app_menu.js` re-registers the item for every app. Odoo's
three all define an action `path`, ours do not, so the scoped URL falls back to
`action-<actionID>` the way `navbar.js` and `menu_helpers.js` already do for
the same problem. An app whose menu carries no `web_icon` gives no module to
scope to and keeps Odoo's whole-backend install.

> A controller change only enters the routing map on container restart; the
> `-u` alone ships the JS half.

> Already-installed PWAs recolor and re-icon lazily — Chrome re-reads the
> manifest within about a day; reinstalling is immediate.

### Branded transactional email layouts

Two layouts read every identity bit from the `company` variable
(logo via `/web/image/res.company/<id>/logo`, name, website, tagline, footer,
signature, legal links):

- `bluefox_branding.bf_mail_layout`
- `bluefox_branding.bf_mail_layout_with_signature`

The literal labels of the layout (`Privacy`, `Terms`, `Unfollow`, and `None`
for a tracked value that is empty) are written in English in the view and
translated from `i18n/fr_CA.po`, so they follow the recipient's language and
not the author's. The unfollow link sits in
`<span id="mail_unfollow">`: without that wrapper `mail.mail.
_personalize_outgoing_body` cannot remove it for a recipient who has nothing to
unfollow, and the email goes out with a token-less `/mail/unfollow`.

These are not overrides of `mail.mail_notification_layout`; the composer wizard
swaps Odoo's default layouts to them for quotes and contracts, and invoices sent
with `account.move.send` (Odoo 18's Send button, which no longer goes through the
composer) take `bf_mail_layout_with_signature`.
Chatter / internal notifications stay on Odoo's default.

The base module `bf_onboarding_base` ships a fallback copy of this layout,
`bf_onboarding_base.bf_mail_layout`, which every in-house module points its
emails to. When this module is installed, the view
`bluefox_branding.bf_mail_layout_relais` replaces that copy's content with
`bluefox_branding.bf_mail_layout`: this module's layout is the one that applies.

The email tagline and the custom footer are translatable, and the layout reads
the company in the language the email is rendered in, on both of Odoo's paths
(a template's `send_mail` and the chatter notifications).

### Odoo's system emails (v18.0.3.26.0)

Part of what Odoo sends never goes through a `mail.template`, so no template
override can reach it. These now wear the brand too:

- **Odoo's "light" layout** (`mail.mail_notification_light`), named in Python by
  the security notices, the two-factor code and invitation, the unregistered-users
  reminder, the calendar's invitations and reminders, and some twenty modules
  (project sharing, surveys, ratings, payment, helpdesk...). An extension view
  replaces its body with a branded card (`bluefox_branding.bf_mail_card`: dark
  header with the brand logo, accent bar, content, company footer). The card only
  reads what every rendering path provides (`company`, `message`,
  `has_button_access`, `button_access`); the light path renders with a minimal
  context in which `bf_mail_layout` would fail. It keeps a leading
  `table[@role='presentation']` for `hr_recruitment`'s extension, and Odoo's
  `mail_unfollow` span, which Odoo personalises for followers and strips for
  everyone else. The chatter layout stays Odoo's.
- **The security notice** (login, password, email, two-factor, trusted devices):
  "Hello" instead of "Dear", a reset button, the two-factor link pointing to
  `/my/security` instead of Odoo's documentation (which `mail_debrand` strips
  anyway, leaving an empty bullet), and "write to us at" the company address
  instead of "contact your administrator".
- **The password reset** (`auth_signup.reset_password_email`) and **the new device
  alert** (`auth_signup.alert_login_new_device`): self-contained QWeb views in
  Odoo 18, both rewritten inside the same card.

Texts of these views are written in English; the French is in `i18n/fr_CA.po`.
The body of the security notice is rendered in the language of the person who
made the change, which is Odoo's behaviour and is left as is.

### Tenant-neutral standard templates

Templates from `om_account_followup`, `contract`, `helpdesk_mgmt`, `survey` and
`calendar` ship `noupdate=1` in their origin module and cannot be patched
declaratively. The `post_init_hook` reads `data/mail_template_overrides.xml`
(French) and `data/mail_template_overrides_en.xml` (English, same records and
structure) and writes the French version in French languages, the English one in
the others. A change to either file only reaches a tenant through a migration
that replays the hook. The
self-contained ones (followups, calendar, survey) and the late-invoice notice
(stock template 141, no XML ID) read **all** identity from `res.company` — no
hardcoded brand values — so they follow whichever company owns the record.
The late-invoice notice lives in `hooks.py` rather than in the data files: one
HTML skeleton (`_LATE_INVOICE_SQUELETTE`) and two sets of sentences
(`_LATE_INVOICE_TEXTES`, French and English), picked by language exactly as the
two XML files are. Until 18.0.3.29.0 it had no English version and the hook
wrote the French body in every active language.
That skeleton carries **no shell** (no `<body>`, header or footer): the notice
renders inside `bf_onboarding_base.bf_mail_layout`, which `_apply_late_invoice`
sets on the template along with `lang`. Putting the shell back would render a
card inside the card, with two brand footers and two rows of legal links.
`_apply_late_invoice` leaves untouched any body that carries `brand_primary`
without being one this module has published (`_LATE_INVOICE_SHA_CONNUS`), so a
tenant's rewording of **our** body survives an upgrade. A body with no
`brand_primary` is not ours (Odoo's own template, or a tenant's own text) and
the hook brands it, as it always has. Each release that changes a sentence must
add its own fingerprints to that set; a test refuses the omission.

Since v18.0.3.26.0 four access templates join them: the new user invitation
(`auth_signup.set_password_email`), the sign-up welcome
(`auth_signup.mail_template_user_signup_account_created`), the two-factor
invitation and the two-factor code by email. A template that the tenant has
edited by hand since its module created it is left as it is (flag
`bf_garde_retouche` in the overrides file, logged when it applies).

The **portal invitation** (`portal.mail_template_data_portal_welcome`) is also
rewritten here since v18.0.3.9.0: the stock template embeds its own Odoo layout
(grey table, purple button, "Powered by" footer), so it is replaced by a short
French body carrying its own "Activate my account" button and
`email_layout_xmlid = bluefox_branding.bf_mail_layout`. A migration replays the
hook so the template is written in every active language.

> Note: the followup / calendar / survey templates are self-contained for
> historical reasons, not because wrapping would not work. Measured on
> 2026-11-12: `mail.template.send_mail()` **does** apply
> `email_layout_xmlid` in Odoo 18. The late-invoice notice relies on it since
> v18.0.3.29.0, and goes out wrapped with a single header card. The calendar's
> invitation, date change and reminder are sent through the light layout, so
> since v18.0.3.26.0 they carry their content only, under a grey eyebrow
> title: with their own frame they came out as a card inside the card.

### Legacy Odoo purple sweep

`mail.mail._send` / `create` / `write` and `mail.render.mixin._render_template`
run a small regex sweep replacing Odoo's legacy `#875A7B` plum (and its
`rgb()` / `#714B67` variants) with the brand primary of the company the email
belongs to. Shared logic lives in `models/brand_color_mixin.py`.

**Which company (18.0.3.30.0).** The record that sends the email, read the way
the core notification layout reads it (`_mail_get_companies`, i.e. the model's
company field); failing that the author of the message; failing that the
session. Until 18.0.3.30.0 all four entry points read `env.company` — the
session's company at creation time, and the queue cron user's at send time —
so a subsidiary's email went out wearing the parent's accent, and two emails
from the same record could differ depending on who triggered them. On the
render path the chain stops at the session on purpose: there the author *is*
the acting user, and the session's company already carries their company-switcher
choice.

A company left without a brand primary keeps the field's default, Odoo's plum,
and the sweep is then a no-op for it. That is deliberate: the company decides
all the way down, and the fix for a subsidiary emailing in plum is to give it a
colour, not to fall back to another company's.

### The "View" button of core notification emails

`data/avis_bouton_marque.xml` (new in 18.0.3.30.0) inherits
`mail.mail_notification_layout` so that button reads
`res.company._brand_email_button_color()`. Odoo's own layout paints it from
`res.company.email_secondary_color` ("Email Button Color"), a core field this
module does not own: it is computed from `secondary_color`, falls back to
Odoo's plum, and nothing keeps it in step with the brand. Measured on one
tenant, 930 of 997 task notifications still in the database carried that
field's value for the parent company rather than the brand accent. The three
views that inherit the layout (responsible signature, invitation, contract)
follow from the same override.

The chain is the brand accent, then the core field, then Odoo's plum. **"No
brand accent" means "left on one of Odoo's own plums"** (`#714B67` or
`#875A7B`, case and whitespace insensitive), not "empty":
`report_brand_primary` has a default, so it is never empty, and a fallback
written behind a non-empty value would be unreachable. A company left on the
default therefore keeps the colour the core field gives it — a subsidiary whose
field says black keeps a black button instead of being repainted plum. An empty
company recordset yields Odoo's plum instead of raising, because the layout can
be rendered without one.

Note the asymmetry with the sweep above, and it is deliberate. In the body, a
company without an accent stays on Odoo's plum: the plum is what was already
there, and the remedy is to give that company a colour. In the notification
layout the button falls back instead, because there the core was already
painting a real colour and dropping it to plum would be a regression.

The raw accent, not its readable variant (`res.company._brand_primary_on_white`):
that is this module's documented choice for email buttons since 18.0.3.19.0,
and already what `bf_mail_layout` does for its own button. Measured under white
text, both the core field's value and the brand accent sit below the AA
threshold on the brand measured, so this changes which colour the button wears,
not whether its contrast passes. The readable variant stays available if that
trade-off is ever revisited.

### Branded PWA manifest

The web app manifests (`/web/manifest.webmanifest` and the scoped per-app
variants) ship Odoo purple hardcoded. `controllers/webmanifest.py` overrides
them so the install splash screen background uses the primary company's
`report_brand_dark` and the status bar (`theme_color`) its
`report_brand_primary`. The primary company is resolved through
`base.main_company` (not sequence order, which archived companies can shadow).
Already-installed PWAs pick the new colors up on the browser's next manifest
refresh; iOS ignores `background_color` for splash screens.

## Dependencies

| Module | Why |
|--------|-----|
| `web`, `mail`, `account`, `sale`, `calendar`, `portal` | Odoo core |
| `om_account_followup`, `contract`, `helpdesk_mgmt`, `survey` | Templates this module rebrands |
| `bf_lexend` | Lexend font + `res.company.font` selection |
| `bf_onboarding_base` | Onboarding panel helper |
| `l10n_ca` | Document layout override for the CA folder layout |

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-11-13, this version converts automatically to
  **LGPL-3.0-or-later**.

## Changelog

### 18.0.3.32.0

- Fixed: the collapsed-menu icon of a site header painted with the brand's dark colour was never reached by this module's rule, so it kept whatever Odoo's own levelled colouring gave it — white at 55% opacity on one bar, where it happens to be visible, and nearly black on a navy one, where it is not. The rule required a `.navbar-toggler` ancestor, and in Odoo 18 that button carries none: it is a `.nav-link.btn` inside `ul.o_header_mobile_buttons_wrap`. The selector therefore matched neither of the two header templates in service, `default` and `mobile`; it matched only `template_header_hamburger`, which Odoo ships and few sites enable. It is now `.navbar-toggler-icon:not(.offcanvas *)`, which covers all three, and the guard that leaves the off-canvas drawer alone is kept.

### 18.0.3.31.0

- The web client's top bar fits a phone: below 768 px the systray may shrink and scroll horizontally rather than push the bar wider than the viewport. See "The web client's top bar at phone width" above.

### 18.0.3.30.0

- The internal lineage's number for the fix published here as 18.0.3.26.1 (the colour of an email's buttons follows the company of the record that sends it). No further change.

### 18.0.3.29.0

- The last literal strings of the mail layout move to an English source translated from the catalogue: `Privacy`, `Terms`, `Unfollow`, and the two `None` of a tracked value left empty. They used to be written in French in the view, so an English recipient read them in French, and a tenant with only `en_US` active read them in French always. In French, the unfollow link now says the catalogue's own wording for `Unfollow`, the one this module already served elsewhere.
- The unfollow link is wrapped in `<span id="mail_unfollow">`. Without that wrapper `mail.mail._personalize_outgoing_body` cannot strip it for a recipient who has nothing to unfollow, and the email goes out carrying a token-less `/mail/unfollow`.
- The late-invoice notice gets its English body, the recipient's language (`{{ object.partner_id.lang }}`, which it did not have while its four neighbours did) and the shared layout. Migration 18.0.3.29.0 rewrites that one template, in every active language, and leaves a body a tenant has edited by hand alone.

### 18.0.3.28.0

- The core calendar invitation names the meeting's agenda when the meeting has one. Migration 18.0.3.28.0 rewrites that one template and nothing else.

### 18.0.3.27.0

- Frame shape per company: corners, shadow and border weight in the Brand Identity panel, applied to the web client, the emails and the PDF. See "Frame shape per company" above. The defaults reproduce the previous appearance.

### 18.0.3.26.1

- Fixed: the plum sweep painted buttons with the *session's* company accent — `env.company` at creation time and the queue cron user's company at send time — instead of the accent of the company the email belongs to. A subsidiary's email went out wearing the parent's colour, and two emails from the same record could differ depending on who triggered them, while the notification layout around them was already reading the record's company. The company is now resolved from the record that sends the email (`_mail_get_companies`), failing that from the author of the message, failing that from the session, on both `mail.mail` and `mail.render.mixin`. A company left without a brand primary keeps Odoo's plum: the company decides all the way down.
- The "View" button of a core notification email now reads the brand primary. Odoo's own layout paints it from `res.company.email_secondary_color`, a core field this module does not own and that nothing keeps in step with the brand: measured on one tenant, 930 of 997 task notifications carried that field's value for the parent company rather than the brand accent, and a subsidiary whose field had been left on Odoo's plum had its button repainted with the session's accent by the sweep above. The brand accent now comes first, the core field remains the fallback, and Odoo's plum the last resort. "No brand accent" means the field was left on one of Odoo's own plums, not that it is empty: `report_brand_primary` has a default, so a fallback written behind a non-empty value would be unreachable, and a company left on the default now keeps the colour the core field gives it instead of being repainted plum. The three views that inherit the layout follow. The raw accent, not its readable variant, consistent with `bf_mail_layout` and with the choice documented in 18.0.3.19.0.

### 18.0.3.26.0

- Odoo's system emails wear the brand: the light layout (security notices, two-factor code and invitation, calendar invitations and reminders, and the notifications of some twenty modules), the security notice text, the password reset and the new device alert. See "Odoo's system emails" above.
- Four access templates join the overrides (new user invitation, sign-up welcome, two-factor invitation and code), except where the tenant edited them by hand.
- The calendar's invitation, date change and reminder lose their own frame, which made a card inside the card under the light layout.
- `post_init_hook(env, only=...)` rewrites the named templates only. Migration 18.0.3.26.0 writes the seven templates this release changes and nothing else: replaying every override would wipe a tenant's later edit of the others.

### 18.0.3.25.1

- The dark header of the branded emails (the common mail layout, the overridden standard templates and the late-invoice notice) now asks for the company's logo for dark backgrounds, `report_brand_logo`, through the `brand` variant of `/brand/logo/<company>`. It falls back to the regular logo when the company has none, so nothing changes for a company without one. Until now that field was read by no email header, and a dark regular logo was invisible on the dark band. The late-invoice notice also leaves `/web/image/res.company/<id>/logo`, which showed a grey placeholder to anonymous readers for a secondary company. Migration 18.0.3.25.1 replaces the address in the stored bodies of the overridden templates, in every language, without replaying the install hook.

### 18.0.3.25.0

- The email tagline (`brand_email_tagline`) and the custom email footer (`brand_email_footer_html`) are translatable. The existing value becomes the source; a company writing in French can give its English emails an English tagline and footer instead of the French ones.
- Fixed: a template sent with `mail.template.send_mail` rendered the layout in the recipient's language but received the company as the caller had read it, so an English email sent by a French-speaking user carried the French tagline even once translated. The chatter notifications pass the language as a render option rather than in the context. An `ir.qweb._render` override now reads the layout's `company` in the render language on both paths; it applies to any mail layout that receives `message` and `company`.

### 18.0.3.24.0

- `bluefox_branding.bf_mail_layout_relais`: when installed, this module replaces the content of `bf_onboarding_base.bf_mail_layout` (the fallback copy shipped by the base module, 18.0.2.1.0 or later) with its own layout, so every in-house module that points its emails to the base layout is dressed by this one.

### 18.0.3.22.0

- Fixed: the header repaint of 18.0.3.21.0 reached only three of Odoo's twelve header templates. Its rules were scoped to *direct* children of `.o_main_nav`, and only "Default", "Rounded box" and "Stretch" put the brand and the `ul.navbar-nav` there. Seven templates ("Vertical", "Search", "Sidebar" and the four "Sales") nest them one to three levels deeper and kept Odoo's light theme on a dark bar; "Hamburger" was covered for its logo but not its desktop links. The four sites checked on 2026-09-18 all happened to use one of the three covered templates, and a header template changes from the website editor in two clicks, without touching code. Measured on the one tenant site using the "Search" template: six links between 1.46:1 and 1.72:1 on a #09445B bar, `filter: none` on the logo, and the bar's three separators in black at 10% opacity, invisible. The rules are now scoped to descendants, and read 8.56:1 and 10.56:1 after the change; the four sites already correct measure identically before and after.
- Fixed: pill backgrounds, `o_border_contrast` borders and `o_header_separator` separators are no longer painted by hand. This module now sets the three custom properties Odoo itself sets when a site's "menu" colour is dark (`--NavLinkWithBackground-bg-color`, `--NavLinkWithBackground-bg-color--hover`, `--HeaderSeparator-bg-color`), which reaches `o_navlink_background_hover` and the separators that the hand-painted rules missed.
- The off-canvas drawer is still never painted, and the guard is now explicit rather than a side effect of the scoping: every selector carries `:not(.offcanvas *)`, and the three custom properties are set on the consuming element instead of an ancestor so they cannot flow into the drawer. The drawer takes its background from the site's "menu" colour — white on two tenants, `#212529` on another — and Odoo colours its text to match; measured at 6.98:1 before and after. Setting the properties on `.navbar` was measured first and put an `o_border_contrast` border in white on a white drawer.
- Known limit, unchanged: the logo is whitened with a filter, which assumes a transparent background; a JPEG on white would come out as a white rectangle.
- Known limit: this module forces the bar dark without telling Odoo, which keeps computing a *light* header. Setting each tenant's "menu" colour to the brand's dark would make Odoo apply `navbar-dark` itself and make this whole block unnecessary. Tracked separately.

### 18.0.3.21.0

- Fixed: a site header painted with the brand's dark colour kept Odoo's light theme on everything sitting on it. Since 18.0.3.13.0 this module paints `#wrapwrap > header .navbar` with `--brand-dark` on every website, while Odoo leaves the header as `navbar-light`: menu links stayed at `rgba(0, 0, 0, .65)` and the logo kept its own colours. Measured on a live site: seven links in pure black on a navy bar, unreadable, and an unfiltered logo. The links, the search icon, the pill backgrounds, the outlined button and the mobile toggler now follow the dark bar, and the logo is whitened with a filter rather than swapped, so a logo changed from the website editor follows without a code change. The rules are scoped to direct children of `.o_main_nav`, which leaves the mobile off-canvas drawer, drawn on white, with its dark text.

### 18.0.3.20.0

- Fixed: the customer portal invitation (`portal.mail_template_data_portal_welcome`) now carries a French subject matching its French body. Since 18.0.3.9.0 the body was overridden in every active language while the subject stayed Odoo's per-language default, so any tenant with an English language active sent "Your account at …" over a French body. The migration replays `post_init_hook`, which is the only path that reaches every language slot of a `noupdate` template.

### 18.0.3.19.0

- Brand colours resolve per tenant at runtime. Every web-client rule Odoo compiles from `$o-brand-primary`/`$o-brand-odoo` is now overridden with `var(--brand-primary)`/`var(--brand-dark)` (generated from the served bundle, dark and accent told apart), so the compile-time defaults no longer leak one company's palette into another tenant. `.btn-primary` and `.bg-primary` use the raw brand accent with white text — a brand-owner decision, documented in the stylesheet, not a contrast oversight. The loading indicator follows `--brand-dark`. New `--brand-primary-on-white-lg` (3:1, large bold text) via `res.company._brand_primary_on_white_large()`.
