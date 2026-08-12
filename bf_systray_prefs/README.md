# Symbifox — Préférences de la barre système

Lets each user **show or hide individual systray icons** (the notification-tray icons in the top-right of the Odoo backend) from a single gear menu. Choices are saved **per user** on `res.users`, so they follow the user across browsers and devices.

On a busy database the systray can accumulate a dozen or more icons (timers, messaging, search, notes, file browsers, etc.). This module gives every user a personal, reversible way to keep only the icons they actually use — without uninstalling anything or affecting anyone else.

## License

LGPL-3 — see the repository [LICENSE](../LICENSE) and `__manifest__.py` for details.

## Features

### Gear menu in the systray
- Adds a single sliders/gear icon to the systray.
- Clicking it opens an **"Afficher dans la barre"** checklist of the other systray icons.
- Ticking/unticking an entry shows/hides that icon immediately; the menu stays open so several can be toggled in one go.
- The gear can never hide itself, so a user always has a way back.

### Generic — adapts to whatever is installed
- The checklist is built **dynamically** from the live systray registry, so it lists whatever icons exist on that database — no per-site configuration.
- Well-known entries get friendly labels; any other entry falls back to a humanized version of its registry key.

### Per-user persistence
- The set of hidden icons is stored as a JSON list on a new `res.users.bf_systray_hidden` field, so the choice **follows the user across browsers and devices**.
- A small `localStorage` mirror (keyed per user id) makes the navbar paint the correct set on first frame, before the authoritative server value returns.
- Defaults to **showing everything** — nothing is hidden until a user opts to, so installing the module changes nothing until each person curates their own tray.

## Requirements

- Odoo 18.0 (Community or Enterprise)
- Module: `web`

## Installation

1. Copy the `bf_systray_prefs` directory into your Odoo addons path.

2. Install the module:
   ```bash
   odoo -d YOUR_DATABASE -i bf_systray_prefs --stop-after-init
   ```

3. Clear the asset cache and restart Odoo:
   ```bash
   psql -U YOUR_DB_USER -d YOUR_DATABASE \
     -c "DELETE FROM ir_attachment WHERE url LIKE '/web/assets/%'"
   systemctl restart odoo  # or docker restart your-odoo-container
   ```

4. Hard-refresh your browser (`Ctrl+Shift+R`).

5. Click the gear icon in the top-right systray and untick any icons you don't want.

## Configuration

No configuration required. The gear is available to all backend users, and each user manages their own visible set.

## Technical Notes

### How It Works
- A JS **service** (`bf_systray_prefs`) holds the reactive list of hidden keys, seeds it from `localStorage`, then reconciles with the server via two `res.users` methods (`bf_get_systray_prefs`, `bf_set_systray_prefs`).
- A **defensive patch** on the core `NavBar.systrayItems` getter filters out the user's hidden keys. The patch wraps `super.systrayItems` in a `try/catch` that falls back to the full list, so a bug here can never take down the systray, and the gear key is always preserved.
- The **gear component** reads the live systray registry to build its checklist, so it needs no static list of icons.

### Security
- `bf_get_systray_prefs` / `bf_set_systray_prefs` are `@api.model` methods that operate **only on `self.env.user`** (the authenticated session's own user) and touch **only** the `bf_systray_hidden` field via `sudo()`. There is no parameter selecting another user, so the pair cannot read or write anyone else's data.
- The stored value is parsed with `json.loads` inside a typed `try/except` (never `eval`), and writes are bounded (max 50 keys, 64 chars each) to prevent a client from bloating its own user row.

## File Structure

```
bf_systray_prefs/
├── __init__.py
├── __manifest__.py
├── README.md
├── models/
│   ├── __init__.py
│   └── res_users.py            # bf_systray_hidden field + get/set RPC methods
└── static/src/
    ├── prefs_service.js        # reactive per-user prefs service (localStorage + RPC)
    ├── navbar_patch.js         # filters NavBar.systrayItems by hidden keys
    ├── systray_gear.js         # gear systray component + dynamic checklist
    ├── systray_gear.xml        # gear dropdown template
    └── systray_gear.scss       # gear + checklist styles
```

## Changelog

### 18.0.1.0.0
- Initial release: per-user systray show/hide via a gear menu, persisted on
  `res.users`, with a dynamic checklist built from the live systray registry.

## Credits

Authored and maintained by Les services de consultation Blue Fox, Inc. AI coding assistants were used as productivity tools during development.

## Support

For issues and feature requests, please contact Les services de consultation Blue Fox, Inc. or open an issue on the project repository.

---

<sub>Authored and maintained by Les services de consultation Blue Fox, Inc. AI coding assistants were used as productivity tools during development.</sub>
