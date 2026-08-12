# BF Timesheet Timer

A modern, OWL 2-based global timesheet timer for Odoo 18 Community & Enterprise. Provides a navbar systray widget with live counters, multi-timer support, quick task selection, and a smart stop dialog with automatic rounding.

## License

LGPL-3 — see [LICENSE](#license-text) below.

## Features

### Global Systray Timer
- Live-updating timer displayed directly in the Odoo navbar, visible from any screen
- Hourglass icon when idle; pulsing green dot + task name + elapsed time when active
- Orange static dot when all timers are paused
- Click anywhere on the widget to open the dropdown (including when a timer is active)
- Timer counter is computed client-side from the start time (no RPC per second)

### Pause / Resume
- Pause a running timer without stopping it — elapsed time freezes
- Resume picks up where it left off (accumulated seconds preserved across multiple pause/resume cycles)
- Pause button (orange) and Resume button (green) next to each timer in the dropdown
- "Pause" badge displayed on paused timers in the dropdown and navbar

### Multi-Timer Support
- Track time on multiple tasks simultaneously
- Badge counter shows the number of active timers
- Optional confirmation dialog when starting a second timer (dismissable with "Don't ask again")

### Recent Tasks (Top 10)
- Dropdown shows your 10 most recently used tasks based on timesheet history
- One-click Play button to start a timer; switches to Stop if a timer is already active for that task
- All task names are clickable for navigation to the task form

### Smart Stop Dialog
- When stopping a timer from the **systray dropdown**, an OWL dialog appears instantly in the current window
- When stopping from a **task form or kanban**, an Odoo wizard dialog opens instantly (no polling delay)
- Both dialogs show:
  - Project and task info
  - Raw elapsed time
  - Editable hours and minutes (pre-rounded based on configurable rounding settings)
  - Clickable **description presets** (configurable chips)
  - Pre-filled description (task name)
- Three actions:
  - **Confirm**: creates a standard `account.analytic.line` timesheet entry
  - **Discard**: deletes the timer without creating a timesheet
  - **Cancel**: re-activates the timer so it continues counting
- The `claimed_at` mechanism ensures only one window shows the dialog (other tabs skip the timer for 5 minutes)

### Description Presets
- Quick-fill chips above the description field in both stop dialogs
- Default presets: Developpement, Reunion, Support, Revision
- Configurable via **Timesheets > Description Presets** menu (managers can add/edit/remove)
- Clicking a preset replaces the description; the field remains freely editable

### Pinned Tasks
- Star icon on each task in the recent tasks list to pin/unpin favorites
- Pinned tasks always appear first in the dropdown, regardless of recent activity
- Per-user: each user manages their own pinned tasks
- Pinned tasks that have no recent timesheets are still shown (fetched via ORM)

### Form & Kanban Buttons
- **Start** (green play) / **Stop** (red stop) buttons on the task form header
- **Start** / **Stop** buttons on each kanban card in the pipeline view
- Buttons appear conditionally based on whether a timer is active for the current user on that task
- Starting a duplicate timer on the same task is blocked with a clear error message

### Visual Indicators
- Pulsing green dot when a timer is active
- Progressive color coding: normal (< 1h), orange (1-2h), red (> 2h)
- Today's and week's total hours displayed in the dropdown footer
- Closed/cancelled tasks shown with strikethrough and reduced opacity
- Browser tab title remains unchanged (no timer overlay on `document.title`)

### Keyboard Shortcut
- `Ctrl+Shift+T` to toggle start/stop the most recent timer

## Requirements

- Odoo 18.0 (Community or Enterprise)
- Modules: `hr_timesheet`, `project`, `base_setup`, `bf_onboarding_base`

## Installation

1. Copy or clone the `bf_timesheet_timer` directory into your Odoo addons path.

2. Update the module list:
   ```
   Settings > Technical > Update Apps List
   ```

3. Install the module:
   ```bash
   odoo -d YOUR_DATABASE -i bf_timesheet_timer --stop-after-init
   ```

4. Restart Odoo and hard-refresh your browser (`Ctrl+Shift+R`).

## Configuration

No configuration is required. The module works out of the box for any user in the **Timesheets / User** group (`hr_timesheet.group_hr_timesheet_user`).

The module automatically resolves the employee record using the same logic as Odoo's native timesheet creation: it first checks `self.env.user.employee_id`, then searches across all companies the user has access to (`self.env.companies`). No manual employee configuration is needed beyond what Odoo already requires for timesheets.

### Rounding Settings

Configurable via **Settings > Feuilles de temps** (visible to Timesheets managers):

| Setting | Options | Default | Description |
|---------|---------|---------|-------------|
| **Mode d'arrondi** | Aucun / Arrondir toujours / Arrondir sous un seuil | Arrondir toujours | Controls when rounding is applied |
| **Increment d'arrondi** | 1 / 5 / 10 / 15 minutes | 5 minutes | Rounding granularity |
| **Seuil d'arrondi** | Integer (minutes) | 30 | Only visible in "Arrondir sous un seuil" mode; durations above this threshold are not rounded |

### Description Presets

Managers (Timesheets / Approver) can add, edit, or remove description presets via **Timesheets > Description Presets**. Regular users can see the presets but cannot modify them.

## Usage

### Starting a Timer

**From the systray dropdown:**
1. Click the hourglass icon in the navbar
2. Find your task in the "Recent Tasks" list
3. Click the green Play button

**From a task form:**
1. Open any task with timesheets enabled
2. Click the **Commencer** button in the header

**From the kanban pipeline:**
1. Find your task card
2. Click the green Play button in the card footer

**Via keyboard:**
- Press `Ctrl+Shift+T` to start a timer on your most recent task

### Stopping a Timer

**From the systray dropdown:**
1. Click the systray widget in the navbar to open the dropdown
2. Click the red Stop button next to the timer

**From a task form or kanban:**
1. Click the **Arrêter** button (the wizard dialog appears instantly)

**Via keyboard:**
- Press `Ctrl+Shift+T` to stop the currently active timer

### The Stop Dialog

When you stop a timer, a dialog appears with:
- The raw elapsed time
- Pre-rounded duration (editable)
- Description field (pre-filled with the task name)

Choose one of:
- **Confirmer** — Saves the timesheet entry and deletes the timer
- **Supprimer** — Deletes the timer without saving anything
- **Annuler** — Closes the dialog and re-activates the timer

## Data Model

### `bf.timer`

Temporary records tracking active and pending timers. Records are deleted after confirmation or discard.

| Field | Type | Description |
|-------|------|-------------|
| `user_id` | Many2one (res.users) | Timer owner, indexed |
| `employee_id` | Many2one (hr.employee) | Associated employee |
| `project_id` | Many2one (project.project) | Task's project |
| `task_id` | Many2one (project.task) | Task being timed |
| `start_time` | Datetime | UTC start time |
| `is_active` | Boolean | `True` = running, `False` = stopped/pending |
| `is_paused` | Boolean | `True` = paused (elapsed frozen), `False` = running |
| `accumulated_seconds` | Float | Total elapsed seconds from previous run segments (before current start_time) |
| `description` | Char | Pre-filled with task name |
| `claimed_at` | Datetime | Set when a wizard claims the timer; prevents multi-window dialogs |

### `bf.timer.description.preset`

Configurable presets for quick-fill description chips.

| Field | Type | Description |
|-------|------|-------------|
| `name` | Char | Display label on the chip |
| `text` | Char | Text injected into the description field |
| `sequence` | Integer | Display order |
| `active` | Boolean | Soft-delete support |

### `bf.timer.pinned.task`

Per-user pinned task favorites. Unique constraint on `(user_id, task_id)`.

| Field | Type | Description |
|-------|------|-------------|
| `user_id` | Many2one (res.users) | Owner, indexed |
| `task_id` | Many2one (project.task) | Pinned task, cascade delete |
| `sequence` | Integer | Display order |

### `bf.timer.stop.wizard` (TransientModel)

Wizard for the instant stop dialog from form/kanban buttons.

| Field | Type | Description |
|-------|------|-------------|
| `timer_id` | Many2one (bf.timer) | Timer being stopped |
| `project_name` | Char | Read-only display |
| `task_name` | Char | Read-only display |
| `elapsed_display` | Char | Formatted raw elapsed time |
| `hours` / `minutes` | Integer | Suggested duration (editable) |
| `description` | Char | Pre-filled, editable |
| `preset_id` | Many2one (bf.timer.description.preset) | Onchange populates description |

### `project.task` (inherited)

| Field | Type | Description |
|-------|------|-------------|
| `bf_has_active_timer` | Boolean (computed) | Whether the current user has an active timer on this task |

### Output

Timesheets are created as standard `account.analytic.line` records — fully compatible with Odoo's native timesheet reports, invoicing, and all third-party modules.

## API Reference

All methods are `@api.model` on `bf.timer` and can be called via RPC:

```python
# Get active timers for the current user
env["bf.timer"].get_active_timers()
# Returns: [{"id", "project_name", "task_name", "task_id", "start_time_iso", "elapsed_seconds", ...}]

# Get top 10 recent tasks (pinned first, then recent, with is_closed/is_pinned flags)
env["bf.timer"].get_recent_tasks(10)
# Returns: [{"task_id", "task_name", "project_id", "project_name", "is_closed", "is_pinned", ...}]

# Start a timer (raises UserError if duplicate on same task)
env["bf.timer"].start_timer(task_id)

# Stop a timer (returns data for confirmation dialog, sets claimed_at)
env["bf.timer"].stop_timer(timer_id)

# Pause a running timer (freezes elapsed, accumulates seconds)
env["bf.timer"].pause_timer(timer_id)

# Resume a paused timer (resets start_time to now, keeps accumulated_seconds)
env["bf.timer"].resume_timer(timer_id)

# Get rounding configuration
env["bf.timer"].get_rounding_settings()
# Returns: {"mode": "round_all", "increment": 5, "threshold": 30}

# Confirm and create timesheet
env["bf.timer"].confirm_timesheet(timer_id, duration_hours, description)

# Discard without creating timesheet
env["bf.timer"].discard_timer(timer_id)

# Re-activate a stopped timer (clears claimed_at)
env["bf.timer"].reactivate_timer(timer_id)

# Get today's / this week's total hours
env["bf.timer"].get_today_total()
env["bf.timer"].get_week_total()

# Get active description presets
env["bf.timer"].get_description_presets()
# Returns: [{"id", "name", "text"}]

# Pin/unpin a task as favorite
env["bf.timer"].pin_task(task_id)
env["bf.timer"].unpin_task(task_id)
```

## Customization

### Changing Rounding Behavior

Rounding is now configurable via **Settings > Feuilles de temps** (no code changes needed). See [Rounding Settings](#rounding-settings) above.

### Changing the Number of Recent Tasks

Edit `static/src/js/bf_timer_service.js`:

```javascript
async function getRecentTasks() {
    return orm.call("bf.timer", "get_recent_tasks", [10]); // Change 10 to desired limit
}
```

### Changing Color Thresholds

Edit `static/src/js/bf_timer_systray.js`:

```javascript
getTimerClass(timer) {
    const elapsed = this.getElapsed(timer);
    if (elapsed >= 7200) return "bf-timer-danger";   // > 2h → red
    if (elapsed >= 3600) return "bf-timer-warning";   // > 1h → orange
    return "bf-timer-normal";
}
```

## Technical Notes

### Odoo 18 Compatibility
- Uses OWL 2 (`@odoo/owl`) component architecture
- Handles JSONB `name` fields (multi-language) in raw SQL queries
- Uses `Dropdown` component with `beforeOpen.bind` for lazy loading
- Registers systray with `force: true` to override existing entries

### Performance
- Timer counter is computed client-side from `start_time` (no RPC per tick)
- Service polls every 5 seconds (4 lightweight RPC calls) for pending timer detection
- Fallback poll every 60 seconds from the service layer
- `get_recent_tasks` uses a single aggregated SQL query + pinned task merge
- `_compute_bf_has_active_timer` uses a single SQL query with `ANY(%s)` for batch computation
- Description presets are cached client-side after the first load

### Security
- All RPC methods verify `user_id == current user` before any operation
- Only users in `hr_timesheet.group_hr_timesheet_user` can access `bf.timer`
- No `sudo()` calls — all operations run under the current user's permissions

## File Structure

```
bf_timesheet_timer/
├── __init__.py
├── __manifest__.py
├── README.md
├── data/
│   └── bf_timer_preset_data.xml         # Seed description presets
├── models/
│   ├── __init__.py
│   ├── bf_timer.py                      # bf.timer model + project.task extension
│   ├── bf_timer_description_preset.py   # Description preset model
│   ├── bf_timer_pinned_task.py          # Pinned task model
│   └── res_config_settings.py           # Rounding settings
├── wizard/
│   ├── __init__.py
│   ├── bf_timer_stop_wizard.py          # Stop wizard TransientModel
│   └── bf_timer_stop_wizard_views.xml   # Wizard form view
├── security/
│   └── ir.model.access.csv             # ACLs for all models
├── static/
│   └── src/
│       ├── js/
│       │   ├── bf_timer_service.js      # Reactive OWL service
│       │   ├── bf_timer_systray.js      # Systray component
│       │   └── bf_timer_stop_dialog.js  # Stop confirmation dialog
│       ├── xml/
│       │   ├── bf_timer_systray.xml     # Systray template
│       │   └── bf_timer_stop_dialog.xml # Dialog template
│       └── scss/
│           └── bf_timer.scss            # Animations, colors, layout
├── views/
│   ├── project_task_views.xml           # Form + kanban buttons
│   ├── bf_timer_description_preset_views.xml  # Preset management views
│   └── res_config_settings_views.xml    # Rounding settings UI
└── docs/
    ├── SERVICE_NOTE_2026-02-12.md       # Initial release note
    ├── SERVICE_NOTE_2026-02-13.md       # v1.3.0 release note
    └── SERVICE_NOTE_2026-02-14.md       # v1.4.0 — titlebar removal
```

## Changelog

### 18.0.1.8.1
- **Dropped the `sh_task_time_adv` (Softhealer) dependency**: the module no longer depends on the proprietary Softhealer timer. Removed the two `view_task_*_hide_sh_timer` inherited views (their only purpose was to hide Softhealer's Start/End buttons). The native OWL timer is self-contained and needs neither. Fixed the `license` metadata comment (LGPL-3, not MIT).

### 18.0.1.8.0
- **Systray bulk stop**: "stop-all" and "stop-red" buttons added to the systray dropdown to stop every running timer (or only the over-threshold red ones) at once.

### 18.0.1.7.0
- **Onboarding panel**: added a `bf_onboarding_base` onboarding step for the timer; now depends on `bf_onboarding_base`.

### 18.0.1.6.0 (2026-03-04)
- **Pause / Resume**: Pause a running timer without stopping it; elapsed time freezes and resumes from where it left off. Pause/resume buttons in the systray dropdown, orange dot indicator when all timers paused.
- **Configurable rounding**: Settings > Feuilles de temps lets managers choose rounding increment (1/5/10/15 min) and mode (none / round all / round below threshold).
- **Selective rounding**: "Round below threshold" mode only rounds durations shorter than a configurable threshold (default 30 min); longer durations pass through unrounded.
- **Dynamic stop dialog**: Minimum rounding message now reflects the configured increment instead of hardcoded 5 minutes.

### 18.0.1.5.0 (2026-02-18)
- **Employee lookup fix**: Aligned employee resolution with Odoo's native timesheet logic. Now uses `self.env.user.employee_id` first, then falls back to searching across all accessible companies (`self.env.companies.ids`) instead of only the current company. Fixes "Aucun employe associe" error for users whose employee record is in a different company.

### 18.0.1.4.0 (2026-02-14)
- **Removed titlebar timer**: The browser tab title no longer displays the elapsed time (`[00:12:34] Page Title`). The tab title stays at the default Odoo value at all times.

### 18.0.1.3.0 (2026-02-13)
- **Instant stop dialog**: Stopping from form/kanban now opens a wizard dialog instantly in the current window only (no more polling delay or multi-window duplicates)
- **Description presets**: Clickable chips (Developpement, Reunion, Support, Revision) above the description field in both stop dialogs; configurable via menu
- **Pinned tasks**: Star icon to pin/unpin favorite tasks that always appear first in the dropdown
- **Comma truncation fix**: Changed `t-on-change` to `t-on-input` on description/hours/minutes fields to prevent value loss during re-renders
- **Closed task styling**: Tasks with state `1_done` or `1_canceled` shown with strikethrough and 50% opacity
- **Duplicate prevention**: Starting a second timer on the same task raises an error
- **`claimed_at` mechanism**: Prevents other browser windows from picking up a timer that's already being handled by a wizard (5-minute timeout for abandoned wizards)
- New models: `bf.timer.description.preset`, `bf.timer.pinned.task`, `bf.timer.stop.wizard`

### 18.0.1.2.0 (2026-02-12)
- Systray widget now always opens the dropdown on click, even when a timer is active
- Previously, clicking the task name in the navbar navigated to the task form instead of opening the dropdown

### 18.0.1.1.0 (2026-02-12)
- Added week total display alongside today's total in the dropdown footer
- Added task search/filter in the recent tasks list
- Added task stage badge and project color dot in recent tasks
- Added relative date labels for recent tasks

### 18.0.1.0.0 (2026-02-12)
- Initial release
- `bf.timer` model with full lifecycle (start → stop → confirm/discard/cancel)
- OWL 2 systray component with live counter, dropdown, and recent tasks
- Stop dialog with 5-minute rounding and 3-action workflow
- Start/Stop buttons on task form and kanban views
- Progressive color coding (normal → orange → red)
- Keyboard shortcut `Ctrl+Shift+T`
- Pending timer detection for form/kanban button stops

## Credits

Authored and maintained by Les services de consultation Blue Fox, Inc. AI coding assistants were used as productivity tools during development.

## License Text

```
This module is licensed under the GNU Lesser General Public License v3.0 (LGPL-3). See [LICENSE](LICENSE) for the full text.
```

## Disclaimer

This module is provided as-is, without warranty of any kind. Use at your own risk. Les services de consultation Blue Fox, Inc. assumes no liability for any damages arising from the use of this software.

## Support

For issues and feature requests, please contact Les services de consultation Blue Fox, Inc. or open an issue on the project repository.

---

<sub>Authored and maintained by Les services de consultation Blue Fox, Inc. AI coding assistants were used as productivity tools during development.</sub>
