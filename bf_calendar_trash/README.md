# BF Calendar Trash

Deleted and archived meetings go to a trash, where they can be restored or
deleted for good. A meeting kept in Nextcloud leaves it when it goes to the
trash, and comes back there when it is restored.

## Why

Odoo's calendar already archived a meeting on its "Delete" button, without
telling Nextcloud: the meeting stayed in Thunderbird and on the phone, and the
next sync that touched it brought it back into Odoo, active and busy. The forms
and lists, on the other hand, erased a meeting for good, with its history.
Cancelling a meeting and then throwing it away took two gestures.

## How it works

- **Calendar > Trash** lists the meetings in the trash: when, by whom, and
  whether they were deleted in Odoo or in Nextcloud. **Restore** puts a meeting
  back; **Delete for good** erases it. Nothing is ever purged on its own.
- What goes to the trash:

  - "Delete" in the calendar, on one meeting or on a series (this date, this
    one and the following ones, the whole series), whichever dialog core opens;
  - "Archive" and "Delete" from a meeting's form or from the list;
  - "Delete" on an activity together with its meeting;
  - "Move to trash" in the cancellation dialog (`bf_calendar_invite`), with the
    same dates as the cancellation;
  - a meeting deleted in Nextcloud or Thunderbird, its series with it.

- Not moved to the trash, with a message: the meeting of an appointment
  (cancel the appointment instead) and an event from a subscribed external
  calendar (change it at its source).
- Deleting a meeting that is already in the trash deletes it for good, after
  removing it from Nextcloud or Google if it is still there; if that fails, it
  stays in the trash and the user is told.
- Only what a person throws away goes to the trash. Odoo's own housekeeping
  (a series rebuilt after a change, a series razed and recreated by the sync)
  still deletes, as before.
- Restoring brings back the whole gesture: a series cut short comes back
  whole, its dates back in their series.
- What Nextcloud is told, after a copy of the resource is kept on the meeting:

  - a meeting of its own, or a whole series: the resource is deleted;
  - one date of a series: the date is excluded (EXDATE);
  - this one and the following ones: the series ends before that date (UNTIL).

  Restoring undoes exactly that: the resource is put back where nothing has
  taken its place, the date's EXDATE is removed, the series' end is put back
  (unless its rule was changed in Nextcloud since). Otherwise the meeting is
  restored in Odoo only, and the user is told.
- A meeting in a Google calendar is deleted from Google, and created there
  again when restored.
- A meeting deleted in Nextcloud and then restored there comes back from the
  trash on its own.
- If Nextcloud cannot be reached, the meeting still goes to the trash, the user
  is told, and the trash shows it as "Still in Nextcloud".
- When the module is installed, every meeting already archived enters the
  trash, dated by its last change.
- The slots held to move a meeting (`bf_calendar_hold`) are released when the
  meeting goes to the trash, as when it is deleted.

## Not included

- Meetings deleted from a Google calendar (still deleted for good).
- "This one and the following ones" done in Nextcloud: the sync recreates the
  series, and the dates it drops are deleted for good.
- Recurring meetings in a Google calendar (the sync does not push series to
  Google either).
- An appointment's meeting deleted in Nextcloud goes to the trash like any
  other: the appointment itself is not cancelled.
- A meeting archived before the module was installed that shares its
  Nextcloud or Google address with another meeting (a duplicate) is deleted
  for good in Odoo only: the resource belongs to the other one too.
- Removing from Nextcloud the meetings archived before the module was
  installed and still there.
- Restoring a series changed in Nextcloud since: Odoo only.

## Version history

- 18.0.1.0.0: first version.
