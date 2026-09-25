# Symbifox Quarts de travail — Shifts (`bf_shift`)

Shift scheduling for regular and unionised employees in Québec, on Odoo 18
Community: working conditions per group, labour-standards checks before a
schedule goes out, call lists, swaps, taxable benefits, and coded hours with
premiums for payroll.

> **Not legal or tax advice.** The module applies the rules described below and
> warns when a schedule departs from them. It does not replace a lawyer, a tax
> specialist or your payroll provider. See [What has not been confirmed](#what-has-not-been-confirmed).

## Why

Odoo Community has no shift planning (the `planning` app is Enterprise), and the
community shift modules plan who works when without any compliance rule. In
Québec, the *Act respecting labour standards* (LNT) sets a floor that every
schedule must respect, and collective agreements add seniority, premiums and a
paper trail that becomes evidence in a grievance. This module carries both.

## Features

- **Working conditions** (`bf.shift.agreement`). A "Labour standards (Québec)"
  record ships with the module. A collective agreement raises the floor:
  weekly and daily overtime thresholds, double time (weekly threshold, seventh
  day, designated days), time bank, minimum call-back, notice period, refusal
  limits, meal break, weekly rest, rest between shifts, premiums, order of
  offers, refusals and swap approval. **A value less favourable than the LNT is
  replaced by the LNT**, and the form says so.
- **Premiums**: time window (may wrap past midnight), whole days or public
  holidays; percentage of the usual rate with a floor per hour, or an amount
  per hour; enhanced rate above a number of hours per block of days; majority
  rule; dates in force. An unpaid break is spread over the shift pro rata.
- **Shift templates** and **schedules** (draft, published, closed), filled
  from templates with an "Add shifts" assistant, and copied to the next period.
- **Checks before publishing**, which warn and never block: 5-day notice, more
  than 2 hours beyond the usual day, more than 14 hours in 24 (12 with flexible
  hours), more than 50 hours in the week (LNT s. 59.0.1), 32 consecutive hours
  of weekly rest (s. 78), meal break after 5 hours (s. 79), overlaps, rest
  between shifts, declared unavailability and open shifts. Weekly limits also
  count shifts from neighbouring schedules.
- **Change log**. Once a schedule is published, every addition, change,
  reassignment or cancellation is recorded (before, after, who, when) and the
  employee is notified. The log cannot be edited or deleted. A change made
  inside the notice period waits for the employee's answer, recorded once.
- **Call lists and offers**. An open shift is offered one person at a time, by
  seniority, rotation (fewest hours offered) or inverse seniority. People
  already working or unavailable are skipped with the reason; every offer,
  refusal and missed deadline is timestamped. A refusal can count as hours
  offered, and a number of refusals removes someone from the list. A scheduled
  action moves on when the answer time runs out.
- **Swaps**: give a shift away or exchange it. The colleague accepts, then a
  manager approves when the working conditions require it. The warnings the
  swap would raise are recorded on the request: the approving manager sees
  them before deciding; without approval, they stay on record.
- **Availability** declared by each employee, weekly or by dates.
- **Taxable benefits**: overtime meals, taxi home, parking, uniforms,
  subsidised meals, with receipts. Each event is marked taxable, not taxable or
  "to check" for Québec and federally, with the reason.
- **Pay periods**: regular hours, overtime, double time, time bank (in and
  out), paid leave, call-back top-up, on-call time, holiday indemnity (1/20 of
  the hours of the four previous weeks at the usual rate, overtime excluded;
  see the limits below), premiums and approved benefits.
  Amounts use the employee's usual hourly rate when it is set. A CSV export is
  attached to the period; an exported period is locked until reopened.
  Deductions, contributions and RL-1 / T4 slips stay with your payroll service.
- **Daylight saving time**: durations are measured in real elapsed time and
  premium windows on the wall clock. The night the clocks go back (22:00 to
  06:00) pays 9 hours; the night they go forward pays 7.

## Roles

| Group | Can |
|---|---|
| **Shifts / Employee** | See published schedules and call lists, their own shifts, offers and changes; answer offers and changes; request swaps; declare availability and benefit events |
| **Shifts / Manager** | Everything else: working conditions, templates, schedules, offers, approvals, pay periods |

Employees get the group explicitly, and their employee record must be linked to
their user. The shift fields on the employee form (working conditions,
seniority, hourly rate, payroll number) are reserved to shift managers, who do
not need HR officer rights. Call lists are **posted**, as collective agreements
require: every employee sees each member's seniority date and counters there.
Records are separated by company.

Answers always go through a method that checks who is answering. On what an
employee creates (availability, benefit events, swaps) they write only the
fields of the form, never a state, a tax verdict or an approver, and the record
must still be theirs after each write. The change log is written by the module
only: managers read it, nobody edits or deletes it, and an answer is recorded
once, with its time and who recorded it. The internal switches the module uses
between its own methods cannot be set by a caller.

## Taxable benefits: what the module applies

| Event | Québec | Federal |
|---|---|---|
| Overtime meal | not taxable if the overtime was requested by the employer, lasted 2 hours or more, happened fewer than 3 times in the week, **with a receipt**, for a reasonable amount | not taxable up to $23, 2 hours or more, fewer than 3 times in the week, no receipt required |
| Taxi home | not taxable on the meal conditions, with a receipt, when there is no public transit or safety is at risk | **to check** |
| Parking | taxable, apart from the exceptions | taxable, apart from the exceptions |
| Uniform, protective clothing | not taxable | not taxable |
| Subsidised meals | **to check** | not taxable at a reasonable price |

Sources: Revenu Québec and Canada Revenue Agency guides, checked September 2026.

## What has not been confirmed

These points should be reviewed by a lawyer or a tax specialist before you rely
on them:

- the scope of LNT s. 59.0.1 for variable schedules;
- the retention period of the hours register (regulation r. 6);
- the federal treatment of a taxi home after overtime;
- the Québec treatment of subsidised meals;
- on-call pay;
- CNESST contributions on these amounts;
- averaging of hours under a collective agreement (s. 53). Averaging is not
  computed; today it is configured by raising the weekly threshold.

## Known limits

- A shift that crosses two weeks counts entirely in the week it starts.
- Holiday indemnity eligibility (unauthorised absence the working day before or
  after) is not checked: the line is produced for every employee of the period.
- Holiday indemnity is computed on hours at the usual rate. LNT s. 62 speaks of
  1/20 of the *wages* of the four weeks, which include premiums: add them in
  payroll if your premiums are significant.
- An offer answered after its deadline is refused; the scheduled action then
  records "no answer" and moves on, every 5 minutes.
- Public holidays are the company-wide time off (without a resource) of the
  working calendar, under Employees › Configuration.
- A start time inside the ambiguous hour of the autumn change is read as
  standard time.

## Configuration

1. Give users the **Shifts / Employee** or **Shifts / Manager** group.
2. Under Shifts › Configuration, review the labour-standards record, add a
   collective agreement if needed, and create shift templates.
3. On each employee (Shifts tab): working conditions, seniority date, usual
   hourly rate, payroll number.
4. Optionally, create call lists and add public holidays to the working
   calendar.

## Requirements

Odoo 18.0 Community. Depends only on core modules: `hr`, `mail`, `resource`.

## Tests

`tests/test_engine.py` covers the rules without the ORM; `tests/test_flows.py`
plays the flows under real Employee and Manager accounts, including what an
employee cannot do by calling the server directly.

## Licence

Business Source License 1.1 (see `LICENSE`): free for your own internal use;
providing it to third parties as a product or service needs an agreement. Each
version becomes LGPL-3 on its Change Date.
