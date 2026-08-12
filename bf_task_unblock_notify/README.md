# BF Task Unblock Notify

An Odoo 18 module that automatically notifies task assignees when their blocked task becomes unblocked. Works with Odoo's native task dependency system (`depend_on_ids`) and sends notifications through the standard mail pipeline (email or inbox, depending on user preference).

## License

LGPL-3 — see [LICENSE](LICENSE).

## Features

### Automatic Unblock Detection

The module detects two scenarios that unblock a task:

1. **Blocker completion**: A blocking task is marked as Done or Cancelled (via direct state change or stage transition), and the dependent task transitions from `04_waiting_normal` to `01_in_progress`.
2. **Dependency removal**: A `depend_on_ids` link is removed from a waiting task, causing it to transition to `01_in_progress`.

Both scenarios trigger an immediate notification to all assigned users of the newly unblocked task.

### Rich Notification Content

Each notification includes:

- **Task name** with a direct link to the task form
- **Project name** and **client name** (from `project.partner_id`)
- **Who** completed the blocking task(s) and **when** (locale-formatted datetime)
- **List of resolved blockers** with their new state label (e.g. "Done", "Cancelled"), project, and client
- Sign-off: "A vous de jouer!"

### Standard Mail Pipeline

Notifications are sent via `message_notify()` — the same API Odoo core uses for task assignment notifications. This means:

- **Email users** (`notification_type='email'`) receive an actual email, wrapped in Odoo's standard notification layout
- **Inbox users** (`notification_type='inbox'`) receive an inbox notification with real-time bus updates
- No manual `mail.notification` creation or bus signaling required

The `mail_notify_author=True` context flag ensures that the user closing the blocker still receives the notification if they are also the assignee of the unblocked task (bypasses Odoo's default author-exclusion filter).

## Requirements

- Odoo 18.0 (Community or Enterprise)
- Module: `project` (standard)
- No custom module dependencies

## Installation

1. Copy the `bf_task_unblock_notify` directory into your Odoo addons path.

2. Update the module list:
   ```
   Settings > Technical > Update Apps List
   ```

3. Install the module:
   ```bash
   odoo -d YOUR_DATABASE -i bf_task_unblock_notify --stop-after-init
   ```

4. Restart Odoo.

## Configuration

No configuration is required. The module works out of the box for any project with task dependencies enabled.

To enable task dependencies on a project, go to **Project > Settings** and enable **Task Dependencies**.

## How It Works

### Detection (`write()` override)

The module overrides `project.task.write()` to intercept state and stage transitions:

1. **Before `super().write()`**: Identifies tasks that are about to close (`1_done` / `1_canceled`) and snapshots their waiting dependents. Also identifies waiting tasks whose `depend_on_ids` are being modified.

2. **After `super().write()`**: Calls `flush_all()` to force Odoo's full recomputation chain (stage change -> blocker state recomputed -> dependent state recomputed), then checks which previously-waiting tasks have transitioned to `01_in_progress`.

3. **Notification**: Calls `_notify_tasks_unblocked()` for each effectively unblocked task.

### Notification (`message_notify()`)

For each unblocked task:

1. Renders the QWeb template `bf_task_unblock_notify.task_unblocked_notification` with full context (task, blockers, closing user, timestamp).
2. Calls `task.message_notify()` with `author_id=OdooBot` and `mail_notify_author=True`.
3. The standard Odoo mail pipeline handles the rest: email delivery for email-preference users, inbox notifications for inbox-preference users, bus notifications for real-time badge updates.

## File Structure

```
bf_task_unblock_notify/
├── __init__.py
├── __manifest__.py
├── LICENSE
├── README.md
├── data/
│   └── unblock_notify_template.xml    # QWeb notification body template
├── models/
│   ├── __init__.py
│   └── project_task.py                # write() override + _notify_tasks_unblocked()
└── docs/
    └── SERVICE_NOTE_2026-02-14.md     # Technical note on notification delivery fix
```

## Changelog

### 18.0.1.7.0
- Documentation and metadata sync (license/LICENSE). See git history for the full detail.

### 18.0.1.6.0 (2026-02-14)
- **Rich notification content**: Notifications now include who completed the blockers, when, the blocker state labels, project names, and client names
- **Sign-off change**: "Bon travail !" replaced with "A vous de jouer !"

### 18.0.1.5.0 (2026-02-14)
- **Fix notification delivery**: Replaced manual `mail.notification.create()` + `_bus_send_store()` with `message_notify()` + `mail_notify_author=True` context. Fixes three cascading issues that prevented notifications from being visible: author exclusion filter, email-preference `is_read=True`, and chatter auto-read.

### 18.0.1.4.0 (2026-02-14)
- Manual notification creation with `_bus_send_store()` (notifications created but silently dropped by the mail pipeline)

### 18.0.1.3.0 (2026-02-14)
- Added `message_post()` with manual `mail.notification` creation

### 18.0.1.2.0 (2026-02-14)
- Added dependency removal detection (scenario 2)

### 18.0.1.1.0 (2026-02-14)
- Added `flush_all()` + `invalidate_recordset()` to handle Odoo's deferred state recomputation

### 18.0.1.0.0 (2026-02-14)
- Initial release: `write()` override detecting blocker completion

## Disclaimer

This module is provided as-is, without warranty of any kind. Use at your own risk. Blue Fox Inc. assumes no liability for any damages arising from the use of this software.

## Credits

Authored and maintained by Blue Fox Inc.

## Support

For issues and feature requests, please open an issue on the project repository.

---

<sub>AI coding assistants were used as productivity tools during development.</sub>
