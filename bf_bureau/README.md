# BF Bureau

User-configurable dashboards ("desks") for Odoo 18: place several Odoo actions side-by-side in a single view, with per-pane view switching, per-desk keyboard shortcuts, automatic time-of-day defaults, and a sidebar of saved desks.

## License

LGPL-3 — see `LICENSE`.

## Concepts

A **desk** (`bf.bureau.desk`) is a named layout combining several **panes** (`bf.bureau.pane`). Each pane points to an existing `ir.actions.act_window` (My activities, All tasks, Inbox, etc.) and renders it in place via Odoo's `<View>` component, with its own filter bar, view switcher, favorites, and click-to-open.

Six layouts are available:

| Layout | Slots | Typical use |
| --- | --- | --- |
| `single` | `full` | A single full-screen pane (focus mode) |
| `two_columns` | `left_full`, `right_full` | Two side-by-side, full height |
| `two_top_one_bottom` | `top_left`, `top_right`, `bottom_full` | 2 on top + 1 wide at the bottom (default) |
| `two_bottom_one_top` | `top_full`, `bottom_left`, `bottom_right` | Inverse: 1 on top + 2 at the bottom |
| `four_quadrant` | `top_left`, `top_right`, `bottom_left`, `bottom_right` | 2×2 grid |
| `stacked_three` | `row_1`, `row_2`, `row_3` | Three stacked rows |

## Features

### Layout
- **Six predefined layouts** selectable from the desk form; a `_check_slot_layout` constraint validates that a pane's slot is compatible with its parent desk's layout.
- **Weights 1–4** per pane (`bf.bureau.pane.weight`) → `fr` ratios computed JS-side for `grid-template-rows / -columns`. Lets you resize a pane without changing the layout.
- **Per-pane domain and context** (`domain_override`, `context_override`): Python expressions (validated server-side via `ast.literal_eval`) applied as an `AND` / `merge` on top of the action's. Same action, different angles in different desks.

### Navigation
- **Per-pane view switcher**: kanban / list / form / pivot / graph / calendar / activity (depending on the action's `view_mode`). State persisted to `bf.bureau.pane.view_type` when clicking "💾 Save layout".
- **Click on a record** opens the full-screen form view via Odoo's standard action service (preserves breadcrumbs and search context).
- **"New" button** launches the action's blank form.
- **Per-pane 🔄 button** forces a full reload (re-mount of `<View>` via `t-key`).
- **Favorite filters** scoped to the embedded action (not the desk client): a `BfBureauPaneView` wrapper does `useSubEnv({ config: { actionId, getDisplayName } })` so `ir.filters.create_or_replace` saves under the right `action_id`. `loadIrFilters: true` loads favorites at mount.

### Multi-desk
- **Sidebar** (`bf-bureau-sidebar`) listing all the user's desks, with a "default" star, a keyboard-shortcut chip, and highlight on the active desk. Visibility persisted in `localStorage` (key `bf_bureau.sidebar.visible`).
- **Per-desk keyboard shortcuts** (`bf.bureau.desk.shortcut_key`, e.g. `alt+1`) — registered through Odoo's `hotkey` service in `global` mode so they work from any view. The closures returned by `hotkey.add()` are stored and called in `onWillUnmount`.
- **Time-of-day** (`bf.bureau.desk.active_when` ∈ {`always`, `morning` 5–12, `afternoon` 12–18, `evening` 18–24, `night` 0–5}). `get_default_desk_id` first opens the desk whose slot covers the current hour, then falls back to `is_default`, then to the first desk.
- **Duplicate** (`action_duplicate_for_me`) clones the desk and its panes for the current user — useful for A/B-testing a layout without losing the original.

### Security
| Risk | Mitigation |
| --- | --- |
| Seeing another user's desks | `ir.rule` `[('user_id', '=', user.id)]` on `bf.bureau.desk`, cascading via `desk_id.user_id` on `bf.bureau.pane`. Admins (`base.group_system`) bypass for support. |
| Multiple default desks | SQL exclusion constraint: `EXCLUDE (user_id WITH =) WHERE (is_default AND active)`. |
| Conflicting keyboard shortcuts | SQL exclusion: `EXCLUDE (user_id, shortcut_key)` when non-empty. |
| Slot incompatible with layout | `@api.constrains("slot", "desk_id")` ⇒ `_check_slot_layout`. |
| `view_type` not supported by the action | `@api.constrains("view_type", "action_id")` ⇒ `_check_view_type_in_action`. |
| Malicious `domain_override` / `context_override` | `ast.literal_eval` server-side (no `eval`/`exec`), `isinstance(list)` / `isinstance(dict)` validation. |
| Reading the action client-side | `read_desk_for_render` does `pane.action_id.sudo().read([...])` but only after `desk.check_access_rights("read")` + `check_access_rule("read")` on the desk. |

### Performance
- `read_desk_for_render` returns everything in a single ORM call (1 round-trip instead of N+1).
- `actionService.loadAction()` runs in parallel for all panes via `Promise.all`.
- `Object.assign(env.config, ...)` mutates the local config of the sub-env, not the parent's — no leak between panes.

## Architecture

```
bf.bureau.desk ─┬─ pane_ids ──> bf.bureau.pane ──> ir.actions.act_window
                ├─ user_id ──> res.users  (record rule scope)
                ├─ shortcut_key (Char, unique per user)
                ├─ active_when (Selection: always / morning / afternoon / evening / night)
                ├─ layout (Selection: 6 values)
                └─ is_default (Boolean, exclusion per user)

bf.bureau.pane ─┬─ slot (Selection: 12 values, validated against layout)
                ├─ view_type (kanban / list / form / pivot / graph / calendar / activity)
                ├─ weight (1–4 → grid-template fr ratio)
                ├─ name_override (Char optional)
                ├─ domain_override (Char, ast.literal_eval list)
                └─ context_override (Char, ast.literal_eval dict)
```

Client side (`static/src/js/bf_bureau_desk.js`):

```
BfBureauDesk (registry "actions" → tag "bf_bureau_desk")
├── _load() : ORM call read_desk_for_render + list_user_desks in parallel
├── _registerHotkeys() : hotkey.add() for each desk.shortcut_key
├── BfBureauPaneView (wrapper per pane)
│   └── useSubEnv({ config: { actionId, actionName, getDisplayName, ... }})
│       └── <View>  (native Odoo, type=kanban/list/...)
└── gridStyle() : compute grid-template-rows/columns from pane weights
```

## Installation

Add the module to Odoo's `addons_path` and install it from the Apps menu. On first install a default desk "My desk" is seeded for `base.user_admin` with three panes (My activities, All tasks, Inbox). `noupdate="1"` ⇒ user changes are not overwritten on subsequent upgrades.

## Dependencies

- `web`, `base`, `mail`, `project` (Odoo core)
- `bf_email_management` (for the Inbox pane in the seeded desk)
- `bf_onboarding_base` (onboarding wizard integration)

## Configuration

- **My desks** (submenu of My desk): create / archive / duplicate / set as default.
- **Editing a desk**: layout dropdown + inline pane table (slot, action, view type, weight, overrides).
- **Sidebar**: ☰ icon in the desk's bar to toggle.

---

<sub>Authored and maintained by Les services de consultation Blue Fox, Inc. AI coding assistants were used as productivity tools during development.</sub>
