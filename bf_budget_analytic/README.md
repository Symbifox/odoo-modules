# Operating Budgets — Analytic Second Axis (`bf_budget_analytic`)

Unfolds a budget line into the analytic lines it reads — each viewer under
their own rights — and puts the hours beside the dollars.

## The problem

The base module already reads the second axis: an accounting line can be
narrowed to analytic accounts, and an internal cost line reads timesheets and
manual analytic entries. What it shows are **sums**. The day someone asks
"which hours are behind this number?", the honest answer is a drill-down — and
a careless drill-down on a labour line is a salary leak, because a timesheet
line carries a cost computed from the employee's hourly rate.

## What it adds

- **A guarded unfold.** The *View analytic lines* button opens exactly what the
  line reads: the action passes a **domain**, never records fetched with
  elevated rights. Access rights and record rules apply at render time, so two
  people unfolding the same labour line can see different records — by design.
  The line's amounts stay sums, computed with elevated rights by the base
  module, identical for every role.
- **One statement, no drift.** The unfold uses the same domain as the measure
  (`_actual_source()` in the base module). What the figure counts and what the
  detail shows cannot diverge.
- **Hours beside the dollars.** An internal cost line shows the timesheet hours
  of the window. A missing hourly rate leaves the dollars at zero — the hours
  stay visible, and the base module already flags uncosted hours.

## Who can unfold, and what each role sees

Reading `account.analytic.line` is granted by the timesheet, analytic
accounting and purchase groups — not by being an internal user. A budget reader
without any of those rights keeps reading the sums — the analytic line count
and the hours included, computed like the base module's amounts — and the
unfold refuses cleanly: the detail is closed, the figures are not.

**What the unfold shows depends on the role, and "own timesheets only" is a
property of a group, not of the unfold:**

| Role | What unfolding a labour line shows |
|---|---|
| Budgets + Timesheets (`hr_timesheet.group_hr_timesheet_user`) | Their own timesheets only. Odoo's record rule restricts the model to `user_id = self`. |
| Budgets + Analytic Accounting (`analytic.group_analytic_accounting`), no timesheet group | **Every** valued timesheet of the company. No record rule is attached to that group on `account.analytic.line`, so only the global company filter applies. |
| Budgets alone | Nothing: the unfold refuses, the sums stay. |

This module **widens no right**: the second role already reads the same records
through Odoo's own *Analytic Items* menu. But the promise has to be stated by
role, because granting analytic accounting is granting the right to read what
colleagues are paid. Decide that on the group, not on this button.

Unfolding a **narrowed accounting line** additionally requires reading journal
items: the shares are filtered through the posted item they are attached to, so
evaluating that domain is a ledger read. Someone with timesheet rights only is
refused cleanly rather than crashing at render time — and the "own timesheets"
rule would have shown them nothing there anyway.

## Licence

Business Source License 1.1 (see `LICENSE`). Each version converts to LGPL-3 on
its Change Date: 2030-11-10 for this one. In the meantime, use in production for
your own internal business operations is permitted.
