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
populated at request time from `request.env.company`. See
`static/src/scss/branding.scss`.

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

These are not overrides of `mail.mail_notification_layout`; the composer wizard
swaps Odoo's default layouts to them for invoices, quotes and contracts.
Chatter / internal notifications stay on Odoo's default.

### Tenant-neutral standard templates

Templates from `om_account_followup`, `contract`, `helpdesk_mgmt`, `survey` and
`calendar` ship `noupdate=1` in their origin module and cannot be patched
declaratively. The `post_init_hook` reads `data/mail_template_overrides.xml`
and writes branded versions over them in every active language. The
self-contained ones (followups, calendar, survey) and the late-invoice notice
(stock template 141, no XML ID) read **all** identity from `res.company` — no
hardcoded brand values — so they follow whichever company owns the record.

> Note: followup / calendar / survey templates are sent via
> `mail.template.send_mail()`, which does not apply `email_layout_xmlid`
> wrapping, so they remain self-contained rather than reusing `bf_mail_layout`.

### Legacy Odoo purple sweep

`mail.mail._send` / `create` / `write` and `mail.render.mixin._render_template`
run a small regex sweep replacing Odoo's legacy `#875A7B` plum (and its
`rgb()` / `#714B67` variants) with the active company's brand primary. Shared
logic lives in `models/brand_color_mixin.py`.

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
- **Change Date**: on 2030-08-12, this version converts automatically to
  **LGPL-3.0-or-later**.
