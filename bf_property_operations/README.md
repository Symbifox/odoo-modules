# Buildings: Operations (`bf_property_operations`)

Odoo's `maintenance` module keeps an equipment register that knows everything
about a device except **where it is**: its only location field is free text,
"Used in location", the kind of thing you fill with "3rd floor" in a workshop.
This module gives it the built environment (the building, the private unit, the
common portion), the teams that answer for each building, a preventive schedule
that belongs to the asset rather than to a ticket, and the work shift with its
handover.

## What it adds

| Where | What |
|---|---|
| `maintenance.equipment` | Building, private unit or common portion, and a readable location. The building is deduced from the unit or common portion (on screen, on create and on write, so an import that names only the unit still passes). Coherence checks: an equipment sits in a unit **or** a common portion, never both; the building matches the one the unit or portion belongs to; the building and the equipment belong to the same company. When the building names a team and the equipment has none, the equipment takes the building's team. |
| `bf.property.building` | The maintenance team that answers for the building. |
| `maintenance.team` | The buildings it answers for, and a team leader who must be a member of the team (the team dashboard and the "my work" filters read the members, so a leader outside the team would receive assignments without seeing them). The occupant-request bridge `bf_property_operations_portal` uses the leader as the default assignee. |
| `maintenance.request` | The building (from the equipment, editable for work that targets no equipment), the preventive schedule it came from, a **Not done** action with its reason and date, and the nature of a corrective repair (routine, or major repair / replacement). |
| `bf.property.maintenance.plan` | The preventive schedule of an asset: frequency (days, weeks, months, years), anchor date, optional end date, next due date, days of advance notice, planned duration and instructions copied onto each work order. A daily scheduled action opens the work that falls due; a button opens it immediately. An "Overdue" filter and column show schedules whose due date has passed without the work being opened. |
| `bf.property.shift` / `bf.property.shift.line` | The work shift: a team, a time span, optionally one building, the people assigned, and the work list with its handover. |
| Reporting | Two entries under the Maintenance reporting menu: the backlog grouped by building and by team (graph, pivot, list), and "what keeps coming back" per asset, which reads the intervention count and MTBF that `maintenance` already carries. |

## It builds on `maintenance`, it does not copy it

`maintenance` is LGPL-3, which the suite's BUSL-1.1 modules may depend on in
this direction; it installs cleanly without demonstration data, and it brings no
group and no portal rule. Writing a second equipment register instead would have
produced two registers of the same assets in one database the day someone
installs `maintenance` for something else, and two registers means every fix has
to be written twice.

Two of `maintenance`'s reports (overall equipment effectiveness and production
losses) measure industrial production, which a building does not have. An
installation hook hides those two menus **once**, and only if they are still
active. An administrator who turns them back on keeps them: no module update
takes them away again. The cost is stated: the hiding applies to every use of
`maintenance` in the database, not only to buildings.

## The two readings of one asset

A boiler has two records, and neither contains the other.

The **regulatory reading** lives in `bf_property_records`, on the maintenance
log a professional establishes under the regulation (CQLR, c. CCQ, r. 8.01):
installation date, required servicing and its stated frequency, routine
repairs, contracts, inspection reports, manufacturer manuals, estimated
condition, remaining useful life, major work with its year and cost.

The **operational reading** lives here, on `maintenance.equipment`: category,
vendor, model, serial number, cost, warranty expiry, team, technician, MTBF,
MTTR, last failure.

Neither is redundant. The failure mode to guard against is not duplication, it
is drift: the day the log still describes a boiler that operations has already
replaced. The bridge that watches for that is a separate add-on,
`bf_property_operations_records`, which installs itself when both sides are
present. This module does not know the maintenance log, so a rental-building
manager gets operations without general meetings, budgets or a contingency
fund.

## Preventive work belongs to the asset, not to the ticket

`maintenance` repeats work **per ticket**: closing one copies the next. That is
a chain, and it stops silently the day nobody closes the ticket, since there is
then no ticket left to look at. A boiler is inspected twice a year whether or
not the previous ticket was closed. The schedule carries that recurrence, and
the nightly pass opens the work that falls due regardless of what happened to
the previous one.

- **A schedule claims nothing from before it.** The start date is an anchor (it
  says whether the inspection falls in March or September), not a reason to
  catch up eleven years of inspections nobody promised. The first due date is
  the first occurrence that is not already past.
- **Each missed occurrence gets its own work order**, up to 24 per schedule in
  one pass: two skipped inspections are two distinct obligations, and each needs
  its own reason.
- **A scheduled work order does not also carry per-ticket recurrence.** Two
  recurrence engines on the same work would each open the next one on closing.
  The combination is refused rather than left to appear as a duplicate.
- **Scheduled preventive work that is not done must say why** (r. 8.01, art. 4).
  Cancelling it goes through **Not done**, which requires the reason, archives
  the work and records the date, rather than through the original archive
  button, which asks nothing.
- **"Corrective" does not say which repair it is.** The regulation separates
  routine repairs (art. 2 para. 2 subpara. 3) from major repairs and
  replacements (art. 3 para. 2, with their cost); Odoo's type covers both. The
  work order carries the nature of the repair, left empty until someone
  decides, and cleared on preventive work. Nothing reaches the log from a
  guessed nature.

The hooks that report completion, non-completion and routine repairs are empty
here; `bf_property_operations_records` fills them.

## The shift, and why the handover is the point

A shift is a team, a time span, people, and a work list composed from open work
orders and due preventive work in the team's sector. The sector is not another
model: it is the set of buildings the team already answers for. A shift on one
building is restricted to it, and that building must belong to the shift's team.

A work list without a handover is what a kanban board already does: unfinished
work simply stays there, and nobody ever has to say what becomes of it. A shift
**closes**, and on closing:

- every item still open goes to a **named** next shift, and the closing shift
  must write a handover note (the list can be read; what matters is what the
  list does not say);
- the next shift must not be closed, must not be the same shift, and must belong
  to a team whose sector covers the buildings being handed over. Work with no
  building passes freely, since no sector claims it;
- a shift that was never opened cannot be closed.

Recomposing the list adds what appeared since; it never removes anything. An
item leaves the list only through **Remove**, which requires a reason, and
shift lines cannot be deleted. A line records only what the shift knows (to do,
settled during this shift, passed on, removed): done, not done and its reason
live on the work order, not twice.

The close wizard proposes the next open shift of the same team; it never
imposes it.

No payroll, no attendance, no rostering: `planning` and `hr_attendance` cover
those. A shift records what work was done during a span, not how many hours
someone worked.

## The notification mode is a default, never an imposition

A caretaker's account created with the Operations group starts in "Handle in
Odoo" (`notification_type = 'inbox'`). Without it the phone stays silent: the
opening message of scheduled work is of type `notification`, and Odoo drops
email-mode recipients for that type. A fresh internal user defaults to email.
The default is detected whether the group arrives through `groups_id`, through
the user form's own group fields, or by implication (a suite manager also holds
Operations).

Odoo 18 drives `notification_type` from membership in
`mail.group_mail_notification_type_inbox`, which makes a one-line alternative
tempting: have the Operations group imply it. That path was rejected. Under an
implication, a person who switches back to email keeps the group membership;
the stored column and the group then contradict each other, and the next
recomputation (triggered by *any* write to that user's groups, including an
administrator granting something unrelated) silently puts them back. Imposing
is arguable and can be disclosed; imposing while appearing not to, on a setting
that belongs to the person, is not.

So the module writes the value once, in `create()`, and never again. An account
that already exists and is later given the Operations group is left alone: it
may carry a deliberate choice. The shift screen names those accounts instead
("they will see the list in Odoo, their phone will not ring") so the question
gets asked of the person rather than settled for them.

## Security: who sees what

- **Operations group** (new, under the property category). It implies
  `maintenance`'s equipment manager group, and it deliberately does **not**
  imply the suite's Consultation group: that group would give a caretaker the
  register of co-owners, which lives on the fraction. Operations opens exactly
  one thing of the structure, read-only: the buildings a maintenance team
  serves, because a work order without its address says what to do without
  saying where. Fractions, ownership and occupant requests stay closed. The
  building fields on work orders and equipment are shown to both groups, so the
  caretaker sees the building on screen and can group by it.
- **The suite manager implies Operations**, so a manager opening a shift is not
  refused on their own portfolio.
- **Buildings.** Record rules combine with OR across a person's groups, and a
  group without a rule opens nothing. Consultation therefore carries its own
  rule on buildings (all of them), next to the Operations rule (buildings with
  a team). Without it, every manager, who also holds Operations through
  implication, would be restricted to buildings that have a team, and the list
  of fractions would raise an access error.
- **Equipment.** `maintenance`'s own rule only shows an employee the equipment
  whose thread they follow. A second rule lets Consultation read equipment
  attached to a building, so a log citing a boiler shows its name instead of an
  access error. Write access is untouched: a record rule has never granted a
  permission the access rights refuse. The unit and common-portion fields stay
  behind Consultation.
- **Preventive schedules** follow the visibility of their equipment, rule for
  rule: followers of the equipment's thread, Consultation for equipment attached
  to a building, equipment managers for all, with the multi-company rule on top.
  Employees read; equipment managers create, edit and delete.
- **Shifts** are visible only within the team: members of the shift's team and
  the people assigned to it (a reinforcement lent for the evening is assigned
  without being a member). The suite manager sees every shift of their
  companies. Multi-company isolation applies on top. Operations may read,
  create and write shifts and lines, never delete them.
- **Coherence checks run in `sudo()`.** The building fields point at models a
  technician cannot read; a check that followed them with the user's rights
  would turn a data rule into an access refusal on a record the user may write.
  The `sudo()` validates; it never displays. Buttons that open lists
  (`action_view_requests`, `action_view_bf_buildings`, `action_view_works`)
  check read access on the record first, since public methods are callable by
  RPC.

Shift names and schedule frequencies are computed at read time, in the reader's
language and time zone, not frozen in the creator's.

## Dependencies

`bf_property_core`, `maintenance`.

## Tests

142 tests: equipment location and coherence, team routing and the leader
guard, preventive schedules (anchor, catch-up, end date, single recurrence
engine, not-done reason), repair nature, backlog and overdue indicators,
building visibility per group, shift composition, closing and handover, team
isolation of shifts, the notification default through every creation path, the
workshop menus hook, and reader-language display.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations, which include administering immovables that you own or
  that you are constituted to administer, and letting a person acting on your
  behalf use your instance for that purpose. A syndicate, a housing cooperative
  or a non-profit housing organisation running the module for its own immovable
  is covered, and so is the bookkeeper or the manager it hires who works inside
  its instance.
- **Requires a written agreement**: administering immovables for the account of
  others, and providing the module as a product or service to third parties,
  whether hosted, managed or resold.
- **Change Date**: on 2030-08-30, this version converts automatically to
  **LGPL-3.0-or-later**.

## Acknowledgements

Created and maintained by Les services de consultation Blue Fox, Inc. AI coding
assistants were used as productivity tools during development.
