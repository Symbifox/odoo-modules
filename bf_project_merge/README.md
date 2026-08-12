# Symbifox — Merging tasks (`bf_project_merge`)

An Odoo **"Merge tasks"** wizard that genuinely consolidates several tasks into
one: instead of archiving duplicates with all their content still inside, it
**moves** that content onto the task you keep, then archives the rest.

## The problem

A merge that simply sets `active = False` on the source tasks leaves behind
everything useful: the chatter conversation, the activities, the hours, the
dependencies. On the surviving task, none of that history is visible any more.
This module fixes that.

## Usage

1. In a task **list** view, tick two or more tasks.
2. Menu **Actions ⚙️ → Merge tasks**.
3. Choose the destination:
   - **Into an existing task** — one of the selected tasks is kept;
   - **Into a new task** — a fresh task is created (title + project).
4. **Merge**. The content moves to the surviving task and the others are
   archived.

## What moves to the surviving task

| Item | Model | Behaviour |
|---|---|---|
| Messages, notes, emails | `mail.message` | Moved — **except** `notification` system messages (stage changes, field tracking), left on the original task as a trace. |
| Scheduled activities | `mail.activity` | Moved. |
| Followers | `mail.followers` | Re-subscribed through `message_subscribe` (no duplicates). |
| Task attachments | `ir.attachment` (`res_model='project.task'`) | Re-pointed at the surviving task. |
| Chatter attachments | `ir.attachment` linked to a message | Follow their moved message (no separate action). |
| Ratings | `rating.rating` | Re-pointed. |
| Linked calendar events | `calendar.event` | Re-pointed. |
| Timesheets | `account.analytic.line` | Re-pointed (`task_id`, with `project_id` aligned on the destination). |
| Dependencies | `project.task` (`depend_on_ids` / `dependent_ids`) | The surviving task inherits the predecessors; successors now point at it (links to the archived task are removed). |
| Sub-tasks | `project.task` (`parent_id`) | Re-parented onto the surviving task. |

Items attached to a `mail.message` (tracking values, notifications, reactions,
starred flags) follow the moved message automatically.

## Security

The wizard is restricted to **Project module users**
(`project.group_project_user`). Before anything moves, the module verifies that
the user may **write** on each of the selected tasks (`check_access('write')`);
sub-record moves then run in `sudo`, with the task as the trust boundary.

## Dependencies

`project` only. `hr_timesheet`, task dependencies, `rating` and `calendar` are
used **when present**, never required.

## Prior art

This module serves the same need as the OCA community module
[`project_merge`](https://github.com/OCA/project) (Onestein), but it is written
independently by Symbifox and adds full content reassignment (conversation,
hours, dependencies, and so on).

## Licence

LGPL-3 — © Les services de consultation Blue Fox, Inc.
