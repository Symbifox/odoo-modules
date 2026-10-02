# BF Work Category (`bf_work_category`)

Classify work into categories (business development, client engagements,
administration…) that every module can filter, group and export, in
**Odoo 18 Community**.

Knowing how many hours went to business development, and not only to which
projects, usually means an export and a spreadsheet. Here a category is set
once, on a project label, and every record that belongs to that work carries it:
the timesheet pivot groups hours by category, and so do Gen conversations,
notes, emails and meeting records.

## How it works

- **A work category is a project label** with *Work category* ticked
  (*Project > Configuration > Tags*). Existing labels keep working: tick the box
  on the one you already use.
- **Each record stores its resolved category** in *Work category*, a regular
  stored field: use it in filters, *Group By*, pivot and graph views, and exports.
  *Category origin* says where it came from.
- **Resolution, strongest first:**
  1. the category forced on the record (*Override category*);
  2. its own category labels (projects and tasks), the lowest *Category order*
     winning when there are several;
  3. its task, then its project (timesheet lines, meeting records);
  4. the record it is attached to (Gen conversations, notes, emails);
  5. otherwise, no category.
- **One category per record**, on purpose: a pivot of hours by category that
  counted the same hour twice would give a wrong total.
- **It stays current by itself.** Change a label, tick or untick a category,
  move a task to another project: projects, tasks, timesheet lines and attached
  records follow.
- **Storing a category is bookkeeping, not an edit.** A record whose only
  change is its category keeps its *Last Updated on* / *Last Updated by*, so
  relabelling a project does not make every task, note and email under it look
  modified.

## What this module covers

- Projects: their category labels.
- Tasks: their category labels, then their project.

The other models come with small bridges, each installed automatically when its
module is present:

| Module | Records | Category from | License |
|---|---|---|---|
| [`bf_work_category_timesheet`](../bf_work_category_timesheet) | timesheet lines | task, then the line's project | LGPL-3 |
| [`bf_work_category_claude_chat`](../bf_work_category_claude_chat) | Gen conversations | the record they are attached to | BUSL-1.1 |
| [`bf_work_category_claude_chat_cockpit`](../bf_work_category_claude_chat_cockpit) | the Gen cockpit's list and search | (views only) | BUSL-1.1 |
| [`bf_work_category_notes`](../bf_work_category_notes) | notes | the record they are attached to | LGPL-3 |
| [`bf_work_category_meeting`](../bf_work_category_meeting) | meeting records | their project | BUSL-1.1 |
| [`bf_work_category_email`](../bf_work_category_email) | emails | the record they are filed on | BUSL-1.1 |

To plug another model, inherit `bf.work.category.mixin` (or
`bf.work.category.linked.mixin` for a `res_model` / `res_id` link), declare
`_bf_work_category_sources`, and create its columns in a `pre_init_hook` with
`bf_work_category.hooks.bf_work_category_prepare_columns` (see any bridge).

## Recompute

Dependencies keep categories current, with one exception: a record attached to
something other than a task or a project (a Gen conversation attached to a
meeting record, for example) reads that record's category when the link is set,
not when that category changes later. A scheduled action, *Work categories:
recompute*, brings every category up to date once a week, in batches, writing
only what differs. Project administrators can run it at once with the
**Recompute categories** button on a category label: it runs in the background,
or right away if the scheduled action is switched off.

## Good to know

- Ticking or unticking a label carried by many projects updates all their
  records in one go, which can take a while on a large database.
- Only project administrators can tick or untick *Work category*, change the
  *Category order* or delete a category; employees can still create, rename and
  delete plain labels.
- A Gen conversation, a note or an email follows the task or project it is
  attached to only if its owner (its user, else its creator) can read that task
  or project: attaching a record to something you cannot open does not tell you
  its category. Access given or taken away later shows at the weekly recompute.
- The resolved category and its origin cannot be written by hand or by an
  import: use *Override category*, which accepts category labels only.
- Installing on an existing database creates the columns first and computes
  only where a label already is a category, in batches, so it stays quick on
  large tables.

## Translations

Source labels are in English; `i18n/fr_CA.po` ships French (Canada).

## License

LGPL-3 (see `LICENSE`).
