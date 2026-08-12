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

Distributed under **LGPL-3**. See the `LICENSE` file.
