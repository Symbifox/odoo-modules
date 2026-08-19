# Symbifox — Productivity Pack

Bundle meta-module for everyday Odoo CE productivity: command palette, multi-pane dashboards, quick notes, timesheet timer, time-of-day slots, daily digest, brand pack, and the Lexend + Dark Mode UI layer.

## Included modules

### Daily-use workflow
| Module | Role |
|---|---|
| [`bf_universal_search`](../bf_universal_search) | Cross-module search via command palette |
| [`bf_bloc_notes`](../bf_bloc_notes) | Quick notes with multi-record links + systray |
| [`bf_bureau`](../bf_bureau) | User-configurable multi-pane dashboards |
| [`bf_timesheet_timer`](../bf_timesheet_timer) | Multi-timer with OWL UI |
| [`bf_time_of_day`](../bf_time_of_day) | Morning/Noon/End-of-day slots for tasks and activities |
| [`bf_task_unblock_notify`](../bf_task_unblock_notify) | Notify assignees when a task becomes unblocked |
| [`daily_todo_digest`](../daily_todo_digest) | Daily activities + tasks digest email |

### Brand & UI layer
| Module | Role |
|---|---|
| [`bf_lexend`](../bf_lexend) | Lexend typeface across UI + PDF reports |
| [`bf_dark_mode`](../bf_dark_mode) | Dark mode using the Symbifox palette |

Installing `bf_productivity_pack` installs all of the above. Uninstalling it does **not** cascade.

### Optional companion

[`bluefox_branding`](../bluefox_branding) (per-company brand colour and email
layout overrides) pairs well with this pack but is **not** installed by it. It
is BUSL-1.1, while this pack and everything it installs are LGPL-3. Pulling it
in would have meant you could not install a permissively licensed pack without
accepting restrictive terms on one of its parts. Install it alongside if you
want it.

## License

GNU LGPL-3. See [`LICENSE`](LICENSE) for the full text.
