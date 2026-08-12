# BF Dark Mode

A dark mode toggle for the Odoo 18 backend, built on the Symbifox brand gray palette. Adds a sun/moon systray button that switches the entire webclient between light and dark themes via a single CSS class on `<body>`.

## License

LGPL-3 — see the repository [LICENSE](../LICENSE) and `__manifest__.py` for details.

## Features

### Systray Toggle
- Sun icon in light mode, moon icon in dark mode
- One-click toggle from the navbar, visible on every screen
- Instant client-side toggle stored in a browser cookie (`bf_color_scheme`)

### Per-user persistence
- A `bf_dark_mode_enabled` boolean is stored on `res.users` and exposed in
  user **Preferences** (and on the full user form for admins)
- The preference is shipped to the web client through the session
  (`session_info`), so the theme is applied **on load without an extra RPC**
- The choice **follows the user across browsers and devices**, and an
  administrator can set it as a default
- On load, the session preference is the source of truth and seeds the local
  cookie/toggle; toggling persists back to `res.users`

### Symbifox Brand Palette
- Surfaces use the BF dark gray (#2E3132) instead of generic navy/black
- Accent color is BF blue (#29ABE2)
- Neutral, warm-gray tones for borders, hover states, and muted text

### Comprehensive Odoo 18 Coverage
- **Bootstrap 5 CSS variable overrides** -- cards, tables, modals, tooltips, popovers, accordions, list groups, and form controls inherit dark colors automatically
- **Odoo 18 component variables** -- `--ListRenderer-*`, `--Kanban-*`, `--formView-*`, `--NavBar-*`, `--ControlPanel-*`, `--settings__*`
- **Bootstrap utility neutralization** -- `.bg-white`, `.bg-light`, `.bg-body`, `.bg-view`, `.text-dark` are all overridden in dark mode context
- **Views**: Form, List, Kanban (grouped & ungrouped), Calendar (FullCalendar v6), Pivot, Graph, Settings, Activity
- **Components**: Navbar, Control Panel, Search Panel, Search Bar, Breadcrumbs, Modals, Popovers, DateTimePicker, Dropdowns, Notifications
- **Chatter & Mail**: Full coverage of Odoo 18 `o-mail-*` classes (Thread, Message, Composer, Activity, Followers, DiscussSidebar, ChatWindow, NotificationItem)
- **Alerts**: Contextual dark variants for info, warning, danger, and success alerts
- **Mobile**: Responsive overrides for small screens

## Color Palette

| Token | Hex | Usage |
|-------|-----|-------|
| `$dk-bg` | `#1a1d1e` | Body / webclient background |
| `$dk-surface` | `#2E3132` | Main surface (sheets, panels, cards) |
| `$dk-surface-alt` | `#363b3c` | Hover states, alternating rows |
| `$dk-raised` | `#3d4344` | Kanban cards, modals, quick-create |
| `$dk-border` | `#4a5153` | Borders and separators |
| `$dk-border-soft` | `#555c5e` | Subtle borders |
| `$dk-text` | `#d1d5d8` | Main text |
| `$dk-text-bright` | `#e8eaec` | Headings, emphasis |
| `$dk-text-muted` | `#8e9496` | Secondary text |
| `$dk-accent` | `#29ABE2` | Symbifox blue (links, active states) |
| `$dk-hover` | `#414849` | Hover highlights |

## Requirements

- Odoo 18.0 (Community or Enterprise)
- Module: `base`

## Installation

1. Copy the `bf_dark_mode` directory into your Odoo addons path.

2. Install the module:
   ```bash
   odoo -d YOUR_DATABASE -i bf_dark_mode --stop-after-init
   ```

3. Clear the asset cache and restart Odoo:
   ```bash
   # Clear cached assets (forces SCSS recompilation)
   psql -U YOUR_DB_USER -d YOUR_DATABASE \
     -c "DELETE FROM ir_attachment WHERE url LIKE '/web/assets/%'"

   # Restart Odoo
   systemctl restart odoo  # or docker restart your-odoo-container
   ```

4. Hard-refresh your browser (`Ctrl+Shift+R`).

5. Click the sun icon in the top-right navbar to activate dark mode.

## Configuration

No configuration required. The toggle is available to all backend users.

## Technical Notes

### How It Works
- The JS component adds/removes the class `bf_dark_mode` on `<body>`
- All SCSS rules are scoped under `body.bf_dark_mode { ... }` -- zero impact when the toggle is off
- The per-user preference (`res.users.bf_dark_mode_enabled`) is the source of truth on load; the browser cookie (`bf_color_scheme`) backs the instant toggle and is re-seeded from the session preference

### Odoo 18 SCSS Constraints
- No SCSS color functions (`lighten()`, `darken()`, `mix()`) are used -- all hex values are pre-computed to avoid libsass compilation issues in Odoo's asset pipeline
- No `rgba()` calls with SCSS variable interpolation inside `#{}` blocks

### Compatibility
- Does not conflict with Odoo's native color scheme (Bootstrap dark mode is disabled in Odoo 18)
- Compatible with the `bluefox_branding` module (navbar colors, primary variables)
- Replaces the third-party `dark_mode_knk` module (Kanak Infosystems) -- uninstall `dark_mode_knk` before installing this module

## File Structure

```
bf_dark_mode/
├── __init__.py
├── __manifest__.py
├── README.md
├── models/
│   ├── res_users.py                # bf_dark_mode_enabled preference field
│   └── ir_http.py                  # session_info: ship pref to web client
├── views/
│   └── res_users_views.xml         # Preferences + admin user form fields
└── static/src/
    ├── js/
    │   └── dark_mode_button.js      # OWL 2 systray toggle component
    ├── scss/
    │   └── dark_mode.scss           # All dark mode styles (~800 lines)
    └── xml/
        └── dark_mode_button.xml     # Systray button template
```

## Changelog

### 18.0.1.1.0
- **Per-user persistence**: added `res.users.bf_dark_mode_enabled`, exposed in
  user Preferences (and the admin user form). The preference is shipped to the
  web client via `session_info` and read on load, so the dark theme now
  follows the user across browsers/devices and can be admin-defaulted. The
  instant client-side cookie toggle is preserved and seeded from the session
  preference; toggling persists back to `res.users`.

### 18.0.1.0.0
- Initial release: per-browser cookie systray toggle and Symbifox dark palette.

## Credits

Authored and maintained by Les services de consultation Blue Fox, Inc. AI coding assistants were used as productivity tools during development.

## Support

For issues and feature requests, please contact Les services de consultation Blue Fox, Inc. or open an issue on the project repository.

---

<sub>Authored and maintained by Les services de consultation Blue Fox, Inc. AI coding assistants were used as productivity tools during development.</sub>
