# Fox Quest: constance time off in Time Off

Bridge between **Fox Quest** (`bf_gamification`) and **Time Off** (`hr_holidays`). It installs itself
when both modules are present.

Fox Quest can offer **constance days off**: the company turns them on and sets the step (for example
60 working days of constance), then each employee chooses to take part from their dashboard. At the
step, a request opens and a manager approves or refuses it.

With this bridge, an approved request becomes an **allocation** in Time Off:

- of the time-off type chosen in **Settings > Fox Quest > Constance**;
- validated at once if the person who approves has that right in Time Off (time off officer, or the
  employee's time off approver);
- otherwise left **to approve** in Time Off: the bridge does not bypass Time Off rights.

Refusing the request in Fox Quest also refuses the allocation, when one has the right to; otherwise a
note on the allocation asks a time off officer to refuse it.

Without a time-off type chosen, or for a user without an employee record, the approval is refused with
a message: it never suggests that a day was granted in Time Off.

The allocation linked to a request is set by the bridge only: nobody changes it by hand. It is
created without the caller's context: a forged context does not validate it at once, its validation
always follows the Time Off rights of the person who approves. Approving or refusing a constance
time-off request is refused while the support sees Symbifox under someone else's account
(impersonation): one must return to one's own account first.

## Requirements

| Dependency | Purpose |
|---|---|
| `bf_gamification` | Fox Quest: constance time-off requests |
| `hr_holidays` | Time Off allocations |

Odoo 18 Community Edition.

## Changelog

### 18.0.1.0.0

- First version. The allocation linked to a request cannot be changed by hand, is created without
  the caller's context (its validation follows Time Off rights), and approving or refusing is refused
  under impersonation.

## License

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-10-24, this version converts automatically to
  **LGPL-3.0-or-later**.
