# BF Activity Board Guard

`mail_activity_board` (OCA) adds a **Document** column to the activity board: a
`Reference` field whose selection lists the models that inherit
`mail.activity.mixin` (`ir.model.is_mail_activity`). Its compute writes
`"<model>,<id>"` into that field without checking the model is in the
selection, so `Reference.convert_to_cache` raises
`ValueError: Wrong value for mail.activity.related_model_instance`.

A computed field is computed for the whole batch being read. **One** activity on
a model that only inherits `mail.thread` therefore breaks, for every user, the
two places a reader reaches: the board's **list** view and the board's **form**
view. (The module also injects the field into Odoo's activity popup, but that
form only reads it for the activity it shows, and an activity on a model
without the mixin has no chatter to be opened from. The board's kanban does not
read the field at all.)

This module overrides the compute so the reference is written only when the
field's own selection accepts the model: `get_values()`, the very call
`convert_to_cache` validates against. A guarded line keeps its summary, type,
deadline and assignee, with an empty Document cell. In the board's form view,
the OCA layout puts that field inside the button that opens the record, so this
module shows the record's name there instead when the reference is empty;
without it, the button would lose its only label.

It does not change which models may carry activities. Giving a model
`mail.activity.mixin` is what makes its activities appear on the record itself,
in the chatter. That is a change to that model's own module, not to this one.
A model with the mixin also loses activities with its records:

- **archiving** a record deletes its **active** activities (`write` and
  `toggle_active` search without `active_test=False`); where
  `bf_activity_cancel_note` is installed, each one is logged in the chatter
  first;
- **deleting** a record deletes **all** of them, archived ones included
  (`unlink` searches with `active_test=False`), and no chatter note survives,
  because the record itself is gone.

Activities reach a model without the mixin through XML-RPC (Odoo's own "Schedule
activity" needs it), or because a module stopped inheriting the mixin while its
activities stayed. Both are normal; neither should break a view. The guard also
replaces, with something versioned, the kind of in-place edit of the OCA file
that this defect invites, an edit no module upgrade and no hygiene pass sees.

**Known limit.** An activity whose `res_model` is empty in the database (a
stored related column that was never computed) still breaks the board's
`open_origin` button with `KeyError: False`; that is Odoo's own code path, and
this module does not touch it.

Depends on `mail_activity_board`.
