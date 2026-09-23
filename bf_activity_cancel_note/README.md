# Symbifox — Activity Cancel Note

In Odoo, **Cancel** on a chatter activity deletes it without a trace. This module
turns that button into a choice:

- **Cancel and log a note** (highlighted): the activity is removed and a note is
  posted in the chatter, modelled on the one "Mark Done" leaves: activity type,
  summary, original assignee, due date, original note, and an optional reason,
  under a cancellation icon. The activity's attachments move to the note.
- **Discard without a trace**: Odoo's behaviour.

Closing the dialog keeps the activity.

Activities removed because their record is archived (the Archive action, a lost
CRM opportunity, an archived project's tasks) also leave a note, marked
"Cancelled automatically".

Activities already done and kept (types with "Keep Done") are never noted.
Archiving many records at once posts one note per open activity: that is the
intent, but it makes a large archive slower than Odoo's plain delete.

A cancellation is not a completion: no chained next activity is created, and
nothing that listens to "Mark Done" (e.g. gamification XP) is triggered.

## Technical notes

- `mail.activity.action_cancel_with_note(reason=False)` is public (the chatter
  calls it by RPC) and checks `unlink` access before posting anything.
- `mail.activity.mixin` is extended on both `write` and `toggle_active`: Odoo
  unlinks the activities in `toggle_active` *before* `write` runs, so the
  `write` override alone misses the Archive action.
- The chatter button (`Activity`) and the list popover button
  (`ActivityListPopoverItem`) are patched; both open the same dialog.

Depends on `mail` only.
