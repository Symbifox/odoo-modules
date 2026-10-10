# BF Task Unblock Notify

An Odoo 18 module that tells the people working on a task when it is no longer blocked. It works with Odoo's native task dependencies (`depend_on_ids`) and sends its notice through the standard mail pipeline (email or inbox, depending on each person's preference).

## License

LGPL-3, see [LICENSE](LICENSE).

## Features

### Unblock detection

Two events unblock a task:

1. **A blocking task closes**: marked Done or Cancelled (directly or through a stage), and the waiting task moves from `04_waiting_normal` to `01_in_progress`.
2. **A dependency is removed**: a `depend_on_ids` link is taken off a waiting task, which then moves to `01_in_progress`.

### The notice

- Wears the company's mail layout when `bluefox_branding` is installed (banner, accent, font, footer), and Odoo's light layout otherwise. The layout signs for the company: the author's own signature is left out.
- Rendered **once per recipient**, in their language and **their time zone** (the time of the unblock used to follow the time zone of whoever closed the blocker), without seconds.
- Names the task, its project and client, its **deadline**, its **planned hours** and a **high priority** when set.
- Lists the tasks that unblocked it, **each one a link**. Their project and client are named only when they differ from the unblocked task's.
- Names **only the tasks the recipient can open**; the others are counted (« Other tasks involved, which you cannot open: 2 »). Version 1.8.0 named any blocker, private project and client included.
- When a dependency is removed, says **who removed it, and which one**.
- A task with **nobody assigned** notifies its **project manager** instead of nobody (setting, on by default).

### Game plan by Gen (optional)

When Gen (`bf_claude_chat`, `bf_ai_bridge`) is installed, the database is allowed to use the plan (system parameter `bf_task_unblock_notify.gen_plan_allowed` = `True`) and the company setting is on, the notice waits for a game plan in the shape of Gen's usual brief: **Situation** (three points at most) and **Next actions** (one to three).

- Odoo assembles what the plan is written from, **as the recipient sees it**: the task, its description, its last messages, its activities, the tasks that unblocked it and the ones it unblocks next. A record the recipient cannot open is left out.
- The bridge writes the plan in a **locked pass** (route `/task-unblock-plan`): no shell, no file access, no tool but the one that hands the plan back. A message from outside in the task's thread can at worst skew the text of the plan.
- The plan is shown as plain text: no markup, three lines at most per block, and **nothing a mail client would turn into a link**: after Unicode normalisation (NFKC) and removal of invisible characters and markup, schemes are replaced by « [...] », and every dot not followed by a space, a closing punctuation or the end of the line becomes « ․ » (U+2024), every @ becomes « ＠ » (U+FF20). A file name or a version stays readable (`server․py`, `6․6․1`), no domain, address or IP stays live. A line with a North American phone number is dropped. Cleaned to a fixed point, cut, cleaned again; the bridge does the same before sending it back. Authors outside the company are marked « (external) », incoming emails « (by email) ».
- **One email**: the notice waits for the plan for about three minutes. Past that, or when anything fails (bridge down, route missing, tenant refused, invalid answer), it leaves without a plan and says so. A cron (every 2 minutes) sends whatever a lost thread left behind, a few minutes later.
- The game plan is allowed per database, by the system parameter: a bridge may serve some databases only, and without the parameter the setting is not even shown.

## Requirements

- Odoo 18.0 (Community or Enterprise)
- Modules: `project`, `bf_onboarding_base`
- Optional: `bluefox_branding` (layout), `bf_claude_chat` and `bf_ai_bridge` (game plan)

## Installation

1. Copy the `bf_task_unblock_notify` directory into your Odoo addons path.
2. Update the module list (Settings > Technical > Update Apps List).
3. Install the module:
   ```bash
   odoo -d YOUR_DATABASE -i bf_task_unblock_notify --stop-after-init
   ```
4. Restart Odoo.

## Configuration

**Project > Configuration > Settings**, next to *Task Dependencies* (which must be on):

- **Unblock notices without assignee**: notify the project manager of an unblocked task that has nobody assigned. On by default.
- **Game plan by Gen**: shown only when Gen is installed and the system parameter `bf_task_unblock_notify.gen_plan_allowed` is `True`. Off by default, per company.

## How It Works

### Detection (`write()` override)

1. **Before `super().write()`**: tasks about to close and their waiting dependents are noted, and so are the links of waiting tasks whose `depend_on_ids` change.
2. **After `super().write()`**: `flush_all()` forces Odoo's recomputation chain, then the module keeps the waiting tasks that moved to `01_in_progress`.
3. **Notice**: one `bf.task.unblock.event` per unblocked task.

### Sending (`bf.task.unblock.event`)

- **Without a game plan**: sent right away, in the transaction that unblocked the task.
- **With a game plan**: recorded as *waiting*; after the commit, **one thread per transaction** takes the notices in turn: it claims each one, reads the context under each recipient's rights, asks the bridge with no database connection held, then sends. A notice is claimed and sent **once** (conditional updates), whoever gets there first: the thread, or the cron (every 2 minutes) for a notice waiting more than 4 minutes or claimed more than 6 minutes ago. A notice the cron fails to send 3 times is marked *Failed*.
- The notice is recorded without the caller's `default_*` context keys: dragging a blocker to Done in a kanban grouped by status no longer reaches it.
- Each recipient gets `message_notify()` with `mail_notify_author=True`, the author being OdooBot.
- Sent and failed notices are purged after 60 days.

## File Structure

```
bf_task_unblock_notify/
├── __manifest__.py
├── data/
│   ├── unblock_notify_template.xml    # QWeb body of the notice
│   ├── ir_cron.xml                    # sends what waited too long for the plan
│   └── bf_onboarding.xml              # onboarding panel (noupdate)
├── models/
│   ├── project_task.py                # write() override: detection
│   ├── unblock_event.py               # the notice: recording, plan, sending
│   ├── res_company.py                 # company settings
│   └── res_config_settings.py
├── views/res_config_settings_views.xml
├── security/ir.model.access.csv
├── migrations/
├── i18n/                              # English source, fr_CA.po
├── tests/
```

## Changelog

### 18.0.2.0.0
- **The company's mail layout** (`bluefox_branding.bf_mail_layout`, else `mail.mail_notification_light`); no more « -- System » signature under the notice.
- **The recipient's time zone**, without seconds. Dates and hours follow the recipient's language even when it is not active in the database (en_US where en_CA serves).
- **Links to the blocking tasks**; their project and client only when they differ.
- **Deadline, planned hours, high priority** in the notice.
- **Who removed which dependency**, when that is what unblocked the task.
- **Project manager notified** when nobody is assigned (setting, on by default).
- **Only the tasks the recipient can open** are named in the notice; the others are counted.
- **Game plan by Gen** (setting, off by default, shown only where the system parameter allows it): one email that waits for the plan about three minutes, written by a locked pass from what the recipient can see, cleaned of anything a mail client would turn into a link.
- Notices are recorded without the caller's `default_*` context keys (a kanban grouped by status lost them).
- **Real settings**: the onboarding panel promised « who is notified and the format of the messages » and opened the general Settings, where the module had nothing. It now opens the Project settings and says what is there. Its text, shipped as `noupdate` data, is switched on upgrade only where it still carries the shipped value.
- The tests of the published copy are part of the module again, run after install.

### 18.0.1.8.0
- **Each assignee reads the notification in their own language.** It used to be rendered once, in the language of whoever closed the blocking task, and sent as is to every assignee: an English-speaking colleague wrote to French-speaking assignees in English. It is now rendered once per language among the assignees.
- **English source strings, French in `i18n/fr_CA.po`.** Odoo never translates into `en_US`, the source language, so while the strings were written in French an English-speaking user received them in French. The template is written in whole sentences, each one a catalogue entry.
- The onboarding panel, shipped as `noupdate` data, switches to English on upgrade only where it still carries the shipped text; an edited value is left as it is.

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

This module is provided as-is, without warranty of any kind. Use at your own risk. Les services de consultation Blue Fox, Inc. assumes no liability for any damages arising from the use of this software.

## Credits

Authored and maintained by Les services de consultation Blue Fox, Inc.

## Support

For issues and feature requests, please open an issue on the project repository.

---

<sub>AI coding assistants were used as productivity tools during development.</sub>
