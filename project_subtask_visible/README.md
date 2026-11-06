# Sub-tasks Always Visible

Sub-tasks appear in the kanban and list views of their project like any other
task, and the eye that hid them is gone.

## Why

In Odoo 18, a sub-task filed in the **same project as its parent** is hidden
from that project's views. The behaviour is carried by a stored flag,
`project.task.display_in_project`, and it is not only a form detail: the seven
task actions of `project` (*Tasks*, *All Tasks*, *Project's tasks*, *Overpassed
Tasks*, *Project Sharing*…) and the counters on the project cards
(`project.project.__compute_task_count`) all filter on
`('display_in_project', '=', True)`.

The flag is flipped by two **eye** buttons on the task form (*Display the
sub-task in your pipeline* / *Hide the sub-task in your pipeline*), shown only
when the technical field `show_display_in_project` is true. So work can be
invisible in the very view that is supposed to show the project, and nothing
says why: on a real database, 1 180 tasks were hidden that way — 1 003
of them still active, 356 in a single project. One of those 1 180 has no parent at
all, and on another database 87 have none: the flag is not set on sub-tasks
only, so what comes back into view is not only sub-tasks.

## What it does

- **Nothing is hidden any more.** `display_in_project` is always true: every
  view, counter and portal page that reads it shows sub-tasks alongside tasks.
  Because they all read the same flag, no action, domain or view had to be
  rewritten.
- **The eye disappears.** `show_display_in_project` is always false, so neither
  of the two icon buttons renders — on the internal form or in project sharing.
- **Writing `false` has no effect.** Install also flips the rows that were
  already hidden, archived ones included. A `false` is dropped from the default
  values, from create and from write — rather than overwritten with `true`,
  because `display_in_project` is readable but **not writable** in project
  sharing, and adding the key to a portal user's values would have had their
  task refused. So the view contexts (`default_display_in_project: False`), a
  project duplication, an import or an XML-RPC call cannot hide a task again,
  and the field is simply recomputed to `true`.
- **A *Without sub-tasks* filter** is added to the task search view, off by
  default. One click folds a crowded project down to its top-level tasks.

## What changes in the behaviour of the core

Two core rules keyed on the flag, and what replaces them here:

| Core (Odoo 18) | With this module |
| --- | --- |
| A **hidden** sub-task follows its parent when the parent moves to another project (`_compute_project_id`); a visible one stays. | A sub-task with no project takes its parent's. When a task moves to another project, the children still filed in its **former** project follow it, down the whole tree; a sub-task filed in a third project stays where it is. |
| Giving a **hidden** sub-task a new parent in another project moved it into that project. | Re-parenting moves nothing: the task stays in the project it was filed in, and it is visible there. |
| Archiving a task archives its **hidden** sub-tasks and leaves the visible ones active (`action_archive`). | Sub-tasks are never hidden, so they stay active when their parent is archived. Deliberate choice: the parent is archived alone. |

## Install

`post_init_hook` sets the flag to true on every task that was hidden, in one
SQL statement, and writes the number of rows it turned to `ir.logging`. The
statement is idempotent, so re-running the install repairs rather than doubles.

Uninstalling does **not** hide anything back: the flag keeps the value the hook
wrote. Take the list of the rows before each deployment (`SELECT id FROM
project_task WHERE display_in_project IS DISTINCT FROM TRUE`), which is what
makes an exact rollback possible.

## Configuration

None. No setting, no group, no menu.

## Credits

Les services de consultation Blue Fox, Inc. — https://symbifox.com
