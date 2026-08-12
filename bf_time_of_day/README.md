# BF Time of Day

Admin-configurable time slots (Morning / Midday / End of day / After hours…)
for Odoo tasks and activities. Each slot carries a name, an icon, a colour and
a suggested time. Choosing a slot on a task rewrites the deadline's time to
match the slot. Each user can override the suggested time with their own
(flex time).

## License

LGPL-3 — see `LICENSE`.

## Features

### Admin-configurable time slots
- A `bf.time.of.day` model (`name`, `code`, `sequence`, `color`, `icon`, `default_time`, `active`).
- 4 seeded presets (`noupdate=1`): **Morning** (09:00, ☕), **Midday** (12:00, 🌞), **End of day** (16:00, 🕓), **After hours** (19:00, 🌙). Renameable, recolourable, extendable — an admin can add as many as they want.
- Admin menu under *Settings → Technical → Time slots*. Inline-editable list with `widget="color_picker"` and `widget="float_time"` down to the minute.

### Applied to `project.task`
- A `time_of_day_id` field (Many2one, indexed, with `group_expand` so empty columns show in kanban).
- On create/write: if `date_deadline` is set AND a slot is chosen, the deadline's **time** is rewritten to the effective time (the **date** is preserved). Explicit UTC ↔ user time zone conversion.
- `default_get` suggests the slot closest to the current local time (wrap-around handled for *After hours*), saving a click in the common case.
- `time_of_day_color` (related, store=True) and `time_of_day_icon` (related) fields for kanban decoration.
- A `time_of_day_code` field (Selection, computed, store=True, indexed): a stable mirror of `time_of_day_id.code` and the key for the kanban progress bar. Limited to the 4 shipped slots — an admin-added slot outside `morning/midday/eod/after_hours` leaves the field empty (and does not appear in the bar).

### Applied to `mail.activity`
- The same `time_of_day_id` field, **purely informational**: `mail.activity.date_deadline` is a `Date` (no time), so nothing is mutated in the data — the slot serves filtering and display.
- If the activity is scheduled from a `project.task` carrying a slot, the slot is **inherited** by default (quick win D).

### Per-user personal override (flex time)
- A `bf.time.of.day.user_pref` model (`user_id`, `time_of_day_id`, `override_time`) with a unique SQL constraint on `(user_id, time_of_day_id)`.
- A *Time slots* tab on the user record (`res.users`) — everyone manages their own lines.
- A `res.users._tod_effective_time(time_of_day)` helper: returns `override_time` when set, otherwise the admin `default_time`, otherwise `False` (no mutation).
- Admin presets are **suggestions**; the personal override wins.

### Kanban visibility ("like the task state")
- Inherits `project.view_task_kanban`: a coloured `o_tag_color_<n>` badge after the tags, prefixed by the slot's Font Awesome icon.
- Inherits `project.view_task_search_form`: 5 filters (`Morning`, `Midday`, `End of day`, `After hours`, `No slot`) plus a *Time slot* group-by.
- A `My day by slot` saved search (quick win C): the user's tasks whose deadline falls today, grouped by slot — one click to see your day.
- The model's display name is prefixed with an emoji (☕ Morning, 🌞 Midday…), so the m2o dropdown reads well without a custom widget.

### "All tasks" kanban view: day on X, slot on Y
- Odoo's kanban has only one axis (columns). A native approximation of a 2D grid on the `/odoo/all-tasks` page:
  - Inherits `project.view_task_kanban_inherit_all_task` (the primary view of the all-tasks action): the stock progress bar on `state` is **replaced** by a bar on `time_of_day_code` (Morning / Midday / End of day / After hours segments, clickable to filter the column). Scoped to that view only — project kanbans and "My tasks" keep the `state` bar.
  - A `Tasks by day` saved search: groups the columns by deadline day (`date_deadline:day`) **and** restricts to tasks carrying a slot (`time_of_day_code != False`). Combined with the bar above: columns = days, bar = time slots, with no stray "Other" segment.
  - Bar colours follow the **arc of the day**: dawn (`info`) → midday (`warning`) → dusk (`danger`) → night (`muted`).
  - **Icons in the bar**: scoped SCSS (an `o_bf_tod_progressbar` class added to the `<kanban>`) injecting a Font Awesome icon as `::before` on each segment (☕ / ☀ / 🕓 / 🌙). Odoo does not render empty segments; a narrow segment crops the icon cleanly and the colours take over. The native tooltip (`{count} {slot}`) is still available on hover.

### Security
| Model | Read | Write / create / unlink |
| --- | --- | --- |
| `bf.time.of.day` (presets) | all internal users (`base.group_user`) | admins only (`base.group_system`) |
| `bf.time.of.day.user_pref` | own user through an `ir.rule` (`user_id == user.id`) | own user through the same rule |
| `bf.time.of.day.user_pref` | admins see everything through a 2nd rule | admins can modify everything |

No `ir.rule` on `project.task` / `mail.activity` — the stock ACL stays in place.
No `sudo()` escalation outside the override lookup (read-only on one's own
preferences).

## Architecture

```
bf.time.of.day (preset, admin)
   ├── used by ──> project.task.time_of_day_id   (mutates date_deadline.time, user TZ)
   ├── used by ──> mail.activity.time_of_day_id  (display + filter, no mutation)
   └── overridden per-user ──> bf.time.of.day.user_pref (user_id, time_of_day_id, override_time)

res.users
   ├── one2many bf_time_of_day_pref_ids
   └── _tod_effective_time(tod) → override_time | default_time | False
```

## Dependencies

- `project` — extends `project.task` (kanban, form, list, search).
- `mail` — extends `mail.activity` (form popup, tree).
- `bf_onboarding_base` — onboarding panel.

## Configuration

- **Seeded presets**: Morning 09:00, Midday 12:00, End of day 16:00, After hours 19:00 (`noupdate=1`, so admin changes survive upgrades).
- **Colours**: the standard Odoo palette (1-11). The seeds use 10 / 3 / 4 / 9.
- **Icons**: Font Awesome 4 classes (`fa-coffee`, `fa-sun-o`, and so on). An emoji mapping is wired for `fa-coffee`, `fa-sun-o`, `fa-clock-o`, `fa-moon-o`, `fa-cutlery`, `fa-bed`, `fa-bolt`, `fa-leaf`, `fa-fire`, `fa-star`. When the icon is not mapped, the display name omits the emoji and keeps just the name.

## Deadline behaviour

| Case | Effect on `date_deadline` |
| --- | --- |
| `time_of_day_id` not set | unchanged |
| `time_of_day_id` set, `date_deadline` not set | unchanged |
| `time_of_day_id` set, `date_deadline` set, `default_time` blank and no override | unchanged |
| `time_of_day_id` set, `date_deadline` set, `default_time` or override set | the deadline's **time** is rewritten, the **date** preserved, the user's time zone respected |

## File Structure

```
bf_time_of_day/
├── __init__.py
├── __manifest__.py
├── README.md
├── LICENSE
├── data/
│   ├── bf_time_of_day_data.xml         # 4 seeded presets (noupdate=1)
│   └── bf_time_of_day_filters.xml      # "My day by slot" + "Tasks by day" ir.filters
├── models/
│   ├── __init__.py
│   ├── bf_time_of_day.py               # the preset model + emoji display_name
│   ├── bf_time_of_day_user_pref.py     # per-user override
│   ├── res_users.py                    # one2many + _tod_effective_time helper
│   ├── project_task.py                 # field + smart default + deadline mutation
│   └── mail_activity.py                # field + slot inheritance from the parent task
├── security/
│   ├── ir.model.access.csv
│   └── bf_time_of_day_security.xml     # ir.rule on user_pref
├── static/src/scss/
│   └── kanban_badge.scss               # kanban chip styling
└── views/
    ├── bf_time_of_day_views.xml        # list + form + action
    ├── res_users_views.xml             # "Time slots" tab on the user record
    ├── project_task_views.xml          # form + kanban + tree + search inheritance
    ├── mail_activity_views.xml         # form popup + tree inheritance
    └── menu.xml                        # Settings → Technical → Time slots (admin)
```

## Changelog

### 18.0.1.3.1 (2026-05-14)
- The `time_of_day_code` progress bar recoloured in soft tones (a pastel arc of the day), segment icons switched to anthracite. The track (`bg-300`) and the "Other" segment (`bg-200`) brought back to a very soft grey. Purely an SCSS change, scoped to `o_bf_tod_progressbar`.

### 18.0.1.3.0 (2026-05-14)
- The `Tasks by day` saved search restricted to `time_of_day_code != False` (and moved out of the `noupdate` block so it stays aligned across upgrades): the progress bar no longer has an "Other" segment, and the column counter only counts scheduled tasks.
- The all-tasks kanban's `time_of_day_code` bar colours follow the arc of the day (`info` → `warning` → `danger` → `muted`).
- Font Awesome icons in the bar segments through scoped SCSS (`o_bf_tod_progressbar` on the `<kanban>`).

### 18.0.1.2.0 (2026-05-14)
- A `time_of_day_code` field (computed Selection, store=True, indexed) on `project.task`, a stable mirror of `time_of_day_id.code`.
- "All tasks" kanban: the `state` progress bar replaced by a `time_of_day_code` bar (scoped to `project.view_task_kanban_inherit_all_task`).
- A `Tasks by day` saved search: kanban columns grouped by deadline day. Combined with the time-slot bar, this gives the requested day-on-X / slot-on-Y approximation.

### 18.0.1.0.0 (2026-05-07)
- Initial release: the preset model (admin) + per-user override + `project.task` extension (time-zone-aware deadline mutation, smart default, group_expand) + `mail.activity` extension (slot inherited from the parent task, display only) + kanban / form / tree / search inheritance with a coloured badge and Font Awesome icon + a "My day by slot" saved search.

## Credits

Blue Fox Inc — https://bluefoxconsultant.com
