# BF Universal Search

A cross-module universal search for Odoo 18 that extends the native command palette (`Ctrl+K`). Type `*` followed by your query to search contacts, projects, tasks, hosting services, documents, tickets, calendar events, and more — all from a single input, on any screen.

## License

LGPL-3 — see [License Text](#license-text) below.

## Features

### Command Palette Integration

The module plugs into Odoo 18's built-in command palette rather than reinventing the wheel. This gives you keyboard navigation (`Up`/`Down`/`Enter`), debounced async search, loading states, and the familiar centered modal — for free.

- **Keyboard**: press `Ctrl+K`, then type `*` followed by your search term
- **Mouse**: click the magnifying glass icon in the systray navbar

The palette footer automatically shows `*enregistrements` alongside the native `/menus`, `@users`, and `#channels` hints, so users can discover it organically.

### Cross-Module Results

A single query searches across all configured models simultaneously. Results are grouped by category with section headers:

| Category | Models searched | Fields |
|----------|----------------|--------|
| **Contacts** | `res.partner` | name, email, phone |
| **CRM** | `crm.lead` | name, partner_name, contact_name, email_from |
| **Projects** | `project.project` | name |
| **Projects** | `project.task` | name (+ record id) |
| **Meetings** | `meeting.record` | name, summary, location |
| **Meetings** | `meeting.agenda` | name, objectives |
| **Communications** | `bf.email` | subject, email_from, body_preview |
| **Communications** | `sms.archive.message` | body, contact_name |
| **Communications** | `call.archive.call` | contact_name |
| **Hosting** | `hosting.service` | name, code, domain_name |
| **Hosting** | `hosting.server` | name, hostname |
| **Hosting** | `hosting.domain` | name |
| **Hosting** | `hosting.software` | name |
| **Documents** | `project.document` | name, code |
| **Documents** | `project.knowledge.matrix` | name, description |
| **Documents** | `project.knowledge.item` | name |
| **Documents** | `corporate.resolution` | name, sequence |
| **Documents** | `bf.sign.request` | name, title |
| **Documents** | `secure.transfer` | name |
| **Finance** | `account.move` | name, ref, payment_reference, invoice_origin |
| **Other** | `helpdesk.ticket` | name, number (+ record id) |
| **Other** | `calendar.event` | name |
| **Other** | `blog.post` | name, subtitle |
| **Other** | `product.template` | name, default_code |

`project.credential` is deliberately absent from the default scope: credential names have no business surfacing in a global search.

Each result shows the record name plus a context line built from the config's `detail_fields` (project · stage, customer · state, date…). Clicking a result opens the record's form view; `Ctrl`+`Enter` opens it in a new browser tab.

### Ctrl+K Opens the Universal Search by Default

Out of the box the palette opens on Odoo's own commands and you type `*` to switch to the universal search. Two settings turn that around, so that `Ctrl+K` lands directly on the `*` namespace and the first keystroke is already the query:

- **Settings → General Settings → Recherche universelle → « Ctrl+K ouvre la recherche universelle »** — the instance default, for every user who has not chosen. Off after install and after upgrade, so nobody's habit changes until an administrator decides.
- **Preferences → « Ctrl+K ouvre »** — each user's own choice: *Selon le réglage de l'instance* (the default), *La recherche universelle (\*)* or *Les commandes Odoo*. A user's choice always beats the instance default, both ways.

The effective value travels with `session_info`, so opening the palette costs no extra request; saving the preferences dialog reloads the page, which is how the change takes effect.

What does not change: `Backspace` on the empty field brings the native command list back, `/` `@` `#` still switch namespaces, the systray magnifier still opens the `*` search whatever the setting, and the **Shortcuts** entry of the user menu still opens the native command list (it asks for its own footer, so it is left alone). An empty `*` palette now says « Tapez au moins deux caractères pour chercher partout » instead of « Aucun résultat trouvé ».

Implementation: `static/src/js/command_palette_patch.js` patches `CommandPalette.setCommandPaletteConfig` and pre-fills `searchValue: "*"` when the config carries no search value, lists the `*` provider, and uses the main palette's default footer. `Ctrl+K` inside the HTML editor and in a chat window call the same `openMainPalette()`, so they follow the setting too.

### Search by Number

Configs with **search_by_id** enabled also match a numeric query against the record id — typing `142` or `#142` jumps straight to that task. Enabled on tasks and tickets by default. A bare number needs at least 2 characters (the palette's floor); the `#` prefix reaches a single-digit id.

### Open Records First, Closed Ones Struck Out

A config can declare a **closed_domain** (e.g. `[('state','in',['1_done','1_canceled'])]` on tasks). Matching records are pushed to the end of their group, greyed out and struck through, and never crowd out live ones: the search fills its slots with open records first, then tops up with closed ones. Enabled out of the box for tasks, tickets, agendas, archived documents, signed/refused signature requests and expired transfers.

### Dynamic Module Detection

The module has **no hard dependency** on hosting_management, project_knowledge_matrix, helpdesk, crm, or any of the searchable business modules. On install, it detects which models are available and creates config entries only for installed ones. Models from uninstalled modules are silently skipped at search time. (It does depend on `bf_onboarding_base`, also in this repo — see Requirements.)

### Admin-Configurable

The search scope lives in `bf.universal.search.config` records. **The module ships no dedicated configuration UI** — manage the records through Odoo's generic model editor (developer mode → the model's technical view) or via shell/SQL/XML data. Each record supports:

- Enable or disable individual models via the **Active** toggle
- Change which fields are searched (`search_fields`) or shown as context (`detail_fields`)
- Restrict what is searchable with a **domain** — e.g. `[('move_type','!=','entry')]` keeps journal entries out of the invoice results. Domains accept relative dates (`context_today()`, `dateutil.relativedelta`), like an `ir.filters` domain
- Mark done/cancelled records with a **closed_domain**
- Set the result **limit**, the **order**, and a **min_length** (raise it to 3-4 on high-volume text models such as SMS or emails)
- Reorder categories via the **Sequence** field
- Add entirely new models without writing code

Only the System-administrator group may create or edit these records.

### Access Control

All searches go through the ORM (`search_read`), which means:

- `ir.model.access` rules are checked before searching any model
- `ir.rule` record-level security is applied automatically
- A user without read access to `hosting.service` will never see hosting results

## Requirements

- Odoo 18.0 (Community or Enterprise)
- No additional Python packages required
- Dependencies: `web`, `base` (core) and `bf_onboarding_base` (in this repo). `bf_onboarding_base` in turn pulls in core `onboarding`.

## Installation

1. Copy both `bf_universal_search` **and** `bf_onboarding_base` into your Odoo addons path (the latter is a dependency, published in this same repo).

2. Install the module:
   ```bash
   odoo -d YOUR_DATABASE -i bf_universal_search --stop-after-init
   ```

3. Restart Odoo and hard-refresh your browser (`Ctrl+Shift+R`) to load the new JS assets.

The `post_init_hook` will automatically create search config records for all installed models listed in the default configuration. Config records are registered in `ir_model_data` with `noupdate=True` to survive Odoo's module cleanup phase and to preserve any admin customizations across upgrades.

## Usage

### Via Keyboard (Recommended)

1. Press `Ctrl+K` to open the command palette
2. Type `*` — the placeholder changes to "Rechercher partout..."
3. Type your search term (minimum 2 characters)
4. Use `Up`/`Down` arrows to navigate results
5. Press `Enter` to open the selected record

### Via Mouse

1. Click the magnifying glass icon in the systray (top-right navbar)
2. The command palette opens pre-filled with the `*` namespace
3. Type your search term
4. Click any result to navigate to it

## Architecture

### Backend

**`bf.universal.search`** (`_auto = False`)

A virtual model (no database table) exposing a single RPC method:

```python
@api.model
def search_all(self, query, model_filters=None, limit=5):
    """
    Returns:
    [
        {
            "model": "res.partner",
            "model_label": "Contacts",
            "icon": "fa fa-users",
            "category": "search_contacts",
            "results": [
                {"id": 42, "name": "Acme Corp", "detail": "info@example.com"},
                ...
            ],
        },
        ...
    ]
    """
```

For each active config entry, the method:
1. Verifies the model exists in the current registry
2. Checks the user's read access via `ir.model.access`
3. Validates that the configured search fields exist on the model
4. Builds an OR domain across all valid fields with `ilike`
5. Calls `search_read` with the configured limit
6. Extracts a display name and a secondary detail from the results

**`bf.universal.search.config`**

Standard Odoo model storing the search configuration:

| Field | Type | Description |
|-------|------|-------------|
| `name` | Char | Display label (e.g. "Contacts") |
| `model_id` | Many2one (`ir.model`) | Target Odoo model |
| `search_fields` | Char | Comma-separated field names (e.g. `name,email,phone`) |
| `detail_fields` | Char | Comma-separated fields shown as the right-hand context line |
| `domain` | Char | Extra domain applied on top of the text search (accepts relative dates) |
| `closed_domain` | Char | Records matching it are listed last, greyed and struck out |
| `order` | Char | `search_read` order (empty = model default) |
| `search_by_id` | Boolean | A numeric query also matches the record id |
| `min_length` | Integer | Minimum query length before this model is queried (default: 2) |
| `icon` | Char | FontAwesome class (e.g. `fa fa-users`) |
| `category` | Char | Grouping key (e.g. `search_contacts`) |
| `sequence` | Integer | Display order |
| `limit` | Integer | Max results per model (default: 5, hard-capped at 20) |
| `active` | Boolean | Toggle to include/exclude from search |

### Frontend

The JavaScript side uses three Odoo 18 registries:

**`command_setup`** — Registers the `*` namespace with a 300ms debounce, French placeholder text, and empty-state message.

**`command_categories`** — Defines the display categories (`search_contacts`, `search_crm`, `search_projects`, `search_meetings`, `search_comms`, `search_hosting`, `search_documents`, `search_finance`, `search_other`) bound to the `*` namespace, each with a label and display sequence.

**`command_provider`** — An async provider that calls `bf.universal.search.search_all` via RPC and transforms each result into a `CommandItem` rendered by a small OWL component (model icon, context line, struck-out when closed), with an `href` for `Ctrl`+`Enter` and an `action` that opens the record's form view.

**Systray component** — A minimal OWL 2 component rendering a magnifying glass button. On click, it calls `commandService.openMainPalette({ searchValue: "*" })` to open the palette pre-configured for universal search.

## Performance

| Aspect | Strategy |
|--------|----------|
| Debounce | 300ms client-side (via `command_setup`) |
| Min query length | 2 characters (server-side floor), raise per model via `min_length` |
| Limit per model | 5 results by default, configurable per config, hard-capped at 20 |
| Queries per search | 1 `search_read` per active model, or 2 when a `closed_domain` is set; at most `_MAX_CONFIGS` (40) models per call |
| Observed latency | ~100–350ms server-side across the ~24 shipped models on a production-size database |
| Concurrency | `@api.readonly` (read-replica routable); Odoo's `KeepLast` displays only the most recent request |
| Index support | Compatible with `base_search_fuzzy` and `base_name_search_improved` for faster `ilike` |

## Customization

### Adding a New Model

No code changes required. Create a new `bf.universal.search.config` record (via the generic model editor in developer mode, shell, SQL, or an XML data file):

1. Set the model, search fields, icon, and category
2. The model will appear in search results immediately

### Changing Categories

Edit the `category` field on config records to regroup models. If you add a category that doesn't exist in the JS registry, results will appear under a generic heading. To add a properly named category, add a line in `universal_search_provider.js`:

```javascript
catReg.add("search_my_category", { namespace: "*", name: _t("Ma catégorie") }, { sequence: 60 });
```

### Adjusting the Debounce

In `universal_search_provider.js`, change the `debounceDelay` value:

```javascript
registry.category("command_setup").add("*", {
    debounceDelay: 500,  // slower typing → fewer requests
    ...
});
```

## Security

| Model | Group | Read | Write | Create | Delete |
|-------|-------|------|-------|--------|--------|
| `bf.universal.search` | Internal User | Yes | No | No | No |
| `bf.universal.search.config` | Internal User | Yes | No | No | No |
| `bf.universal.search.config` | Settings (admin) | Yes | Yes | Yes | Yes |

All searches respect Odoo's standard access control. No `sudo()` calls are used: `search_all` checks `ir.model.access` per model and lets `search_read` apply record rules, so a model the user can't read is skipped. Any unexpected error while searching a model is logged as a warning and that model is dropped from the results rather than failing the whole search. `search_all` is decorated `@api.readonly` (routable to a read replica) and caps both the per-model result count and the number of models consulted per call.

## File Structure

```
bf_universal_search/
├── __init__.py
├── __manifest__.py
├── LICENSE
├── README.md
├── hooks.py                                # post_init_hook + shared config spec
├── data/
│   └── bf_onboarding.xml                   # Onboarding panel step
├── i18n/
│   ├── bf_universal_search.pot
│   └── fr_CA.po
├── migrations/
│   ├── 18.0.1.1.0/ … 18.0.1.3.0/           # Earlier config-seeding migrations
│   └── 18.0.2.0.0/
│       └── post-migrate.py                 # Widens scope, backfills new fields
├── models/
│   ├── __init__.py
│   ├── bf_universal_search.py              # Virtual model with search_all()
│   ├── bf_universal_search_config.py       # Config model
│   ├── ir_http.py                          # Ctrl+K setting in session_info
│   ├── onboarding_onboarding.py            # Onboarding panel hook
│   ├── res_config_settings.py              # Instance default for Ctrl+K
│   └── res_users.py                        # Per-user Ctrl+K preference
├── security/
│   └── ir.model.access.csv
├── tests/
│   ├── __init__.py
│   ├── test_ctrl_k_default.py
│   └── test_universal_search.py
├── views/
│   ├── res_config_settings_views.xml       # Settings block
│   └── res_users_views.xml                 # User form + Preferences
└── static/
    ├── description/
    │   └── index.html
    └── src/
        ├── js/
        │   ├── command_palette_patch.js      # Ctrl+K opens on the * namespace
        │   ├── universal_search_provider.js  # Namespace, categories, provider, item
        │   └── universal_search_systray.js   # Systray magnifying glass
        ├── xml/
        │   └── universal_search.xml          # OWL templates
        └── scss/
            └── universal_search.scss         # Styles (icon, context line, closed)
```

## Design Rationale

Three approaches were evaluated:

1. **Systray dropdown** — Custom dropdown with search field and grouped results. Rejected: limited width, awkward auto-close behavior, reinvents existing infrastructure.
2. **Command palette extension** (chosen) — Extends Odoo 18's native `Ctrl+K` palette with a `*` namespace. Reuses the dialog, keyboard navigation, debounce, loading states, and footer hints. ~200 lines of JS. Same pattern used by `mail` for `@users` and `#channels`.
3. **Inline navbar search bar** — Permanent search field in the top bar. Rejected: consumes scarce navbar space, responsive issues, requires building dropdown + keyboard navigation from scratch.

## Changelog

### 18.0.2.1.0

- **`Ctrl+K` can open the universal search directly** — a per-user preference (*Ctrl+K ouvre*) and an instance default (Settings → Recherche universelle). The user's choice wins over the instance default in both directions; both are off after install and after upgrade, so no existing habit changes.
- The effective value is delivered through `session_info`, so the palette reads it without an extra request.
- An empty `*` palette now invites a longer query instead of reporting no results.

### 18.0.2.0.1

- **Security fix** — the config's domain evaluator was renamed to a private method so it can no longer be reached over RPC (a public name would have let any authenticated user run `safe_eval` on an arbitrary string).
- `search_all` is now `@api.readonly` and hard-caps both the per-model result count (20) and the number of models consulted per call (40); the RPC-supplied `limit` and `model_filters` are validated.
- The upgrade migration now preserves admin customisations (sequence, custom domains) instead of resetting them.

### 18.0.2.0.0

- **Scope widened** from 12 to ~24 models: adds CRM leads, meetings and agendas, communications (emails, SMS, calls), invoices, signature requests, secure transfers, knowledge matrices, corporate resolutions, blog posts and products. New palette categories: CRM, Meetings, Communications, Finance.
- **Search by number** (`search_by_id`) — a numeric query (`142` / `#142`) matches the record id; enabled on tasks and tickets.
- **Open records first, closed ones struck out** (`closed_domain`) — done/cancelled records are sorted last, greyed and struck through by a dedicated palette item component.
- **Per-config `domain`** (with relative-date support), **`detail_fields`** context line, **`order`**, and **`min_length`**.
- `Ctrl`+`Enter` opens a result in a new tab (`href`).
- `project.credential` removed from the default scope.

### 18.0.1.4.0

- Documentation and metadata sync (license/LICENSE). See git history for the full detail.

### 18.0.1.3.0 (2026-02-16)

- **Fix: search returning no results** — Config records created by `post_init_hook` were immediately deleted by Odoo's `_process_end()` cleanup because they were registered in `ir_model_data` with `noupdate=False` but not defined in any XML data file. Changed to `noupdate=True` and added migration script to recreate the 12 configs.
- Improved error visibility: added `console.error` in the JS provider catch block (previously silent)
- Changed Python search error logging from `debug` to `warning` level for production visibility

### 18.0.1.1.0 – 18.0.1.2.0

- Incremental config-seeding fixes (see the corresponding `migrations/` folders and git history).

### 18.0.1.0.0 (2026-02-13)

- Initial release
- `bf.universal.search` virtual model with `search_all()` RPC method
- `bf.universal.search.config` for admin-configurable model inclusion
- Command palette `*` namespace with 5 display categories
- Systray magnifying glass icon
- `post_init_hook` for automatic config seeding
- 12 pre-configured models across contacts, projects, hosting, documents, and more
- Full ACL and record rule enforcement

## Credits

Authored and maintained by [Les services de consultation Blue Fox, Inc.](https://symbifox.com). AI coding assistants were used as productivity tools during development. All code was reviewed, tested, and validated in a production Odoo 18 environment.

## License Text

```
This module is licensed under the GNU Lesser General Public License v3.0 (LGPL-3). See [LICENSE](LICENSE) for the full text.
```

## Disclaimer

This module is provided as-is, without warranty of any kind. Use at your own risk. Les services de consultation Blue Fox, Inc. assumes no liability for any damages arising from the use of this software.

---

<sub>Authored and maintained by Les services de consultation Blue Fox, Inc. AI coding assistants were used as productivity tools during development.</sub>
