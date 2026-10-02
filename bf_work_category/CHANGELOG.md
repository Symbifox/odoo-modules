# Changelog

## 18.0.1.0.3 (2026-10-02)

- What a record's owner may read is checked under the owner's own companies: with several
  companies ticked, filing someone else's email or note on a task no longer fails with
  "Access to unauthorized or invalid companies".
- A record attached to something other than a task or a project follows a change of owner.

## 18.0.1.0.2 (2026-10-02)

- Employees can no longer delete a work category, nor create one through a default value;
  plain labels stay theirs to create, rename and delete.
- A Gen conversation, a note or an email follows its task, project or linked record only if
  its owner can read it: attaching a record no longer reveals the category of a record you
  cannot open, nor that it exists.
- In a write that mixes real edits and category changes, the records whose only change is
  their category keep their *Last Updated on* / *by*.
- The recompute button runs at once when the scheduled action is switched off.

## 18.0.1.0.1 (2026-10-02)

- Storing a category no longer changes a record's *Last Updated on* / *Last Updated by*:
  installing, relabelling a project or recomputing used to stamp every task, timesheet
  line, note and email under it as modified.
- The links a Gen conversation, a note or an email keeps to its task and project are
  for administrators only: they showed the name of a task or a project the reader could
  not open.
- The resolved category and its origin can no longer be written by hand or imported;
  *Override category* accepts category labels only.
- Recompute in batches, writing only what differs, from a weekly scheduled action; the
  button on a category label now starts it in the background.
- Installing creates the columns before the ORM, so nothing is computed on existing
  records until a label is a category.
- A link to an abstract model no longer breaks the recompute.
- The category origins of bridged models are translated.

## 18.0.1.0.0 (2026-09-30)

- First version: work category flag and order on project labels, category mixin with
  stored resolution (override, labels, task, project, linked record), projects and tasks,
  recompute action, French (Canada) translation.
- Resolution reads only the fields it needs (`prefetch_fields=False`): a first install on
  a database with a large email table ran out of memory prefetching it.
