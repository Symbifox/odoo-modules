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
  day, designated days), time bank, minimum call-back, minimum presence,
  on-call pay, averaging of hours, notice period, refusal limits, meal break,
  weekly rest, rest between shifts, premiums, order of offers, refusals and
  swap approval. **A value less favourable than the LNT is replaced by the
  LNT**, and the form says so.
- **Premiums**: time window (may wrap past midnight), whole days or public
  holidays; percentage of the usual rate with a floor per hour, or an amount
  per hour; enhanced rate above a number of hours per block of days; majority
  rule; dates in force. An unpaid break is spread over the shift pro rata.
- **Shift templates** and **schedules** (draft, published, closed), filled
  from templates with an "Add shifts" assistant, and copied to the next period.
- **Checks before publishing**, which warn and never block (LNT s. 59.0.1
  unless stated):
  - 5-day notice;
  - more than 2 hours beyond the usual day, read weekday by weekday in the
    person's working calendar (8 h on Monday and 10 h on Friday is a regular
    schedule), and more than 14 hours in 24;
  - for **variable or non-continuous hours**, only more than 12 hours in 24,
    instead of the two limits above. Variable or non-continuous means the box
    ticked on the employee, a working calendar with flexible hours, or, for
    that day only, a **split shift** (two shifts started the same day at least
    an hour apart);
  - more than 50 hours in the week; under averaging, 50 hours on the average
    of the period, and a week beyond 50 hours under an individual agreement
    (s. 53);
  - 32 consecutive hours of weekly rest (s. 78), meal break after 5 hours
    (s. 79), overlaps, rest between shifts, declared unavailability and open
    shifts.

  Weekly limits also count shifts from neighbouring schedules. On call at
  home is not work (s. 57): it raises no notice warning and asks no answer.
- **Change log**. Once a schedule is published, every addition, change,
  reassignment or cancellation is recorded (before, after, who, when) and the
  employee is notified. The log cannot be edited or deleted. A change made
  inside the notice period waits for the employee's answer, recorded once, and
  the notice says an answer is expected. Extending an announced shift so that
  the day stays within the limits of s. 59.0.1, 1st paragraph, asks no answer
  and keeps the original notice date: the 3rd paragraph gives no right to
  refuse it.
- **Call lists and offers**. An open shift is offered one person at a time, by
  seniority, rotation (fewest hours offered) or inverse seniority. People
  already working or unavailable are skipped with the reason; every offer,
  refusal and missed deadline is timestamped. A refusal can count as hours
  offered, and a number of refusals removes someone from the list. A scheduled
  action moves on when the answer time runs out. The next person's to-do comes
  from the schedule's responsible, never from the colleague who just refused,
  and every notice is written whole in its reader's language.
- **Swaps**: give a shift away or exchange it. The colleague accepts, then a
  manager approves when the working conditions require it. The warnings the
  swap would raise are recorded on the request: the approving manager sees
  them before deciding; without approval, they stay on record.
- **Availability** declared by each employee, weekly or by dates.
- **Taxable benefits**: overtime meals, taxi home, parking, uniforms,
  subsidised meals, provided by the employer or reimbursed on receipts. Each
  event is marked taxable, not taxable or "to check" for Québec and federally,
  with the reason. See the table below.
- **Pay periods**: regular hours, overtime, double time, time bank (in and
  out), paid leave, call-back top-up, 3-hour minimum top-up (s. 58), on-call
  pay, holiday indemnity (1/20 of the hours of the four previous weeks at the
  usual rate, overtime excluded; see the limits below), premiums and approved
  benefits. Amounts use the employee's usual hourly rate when it is set. A CSV
  export is attached to the period; an exported period is locked until
  reopened. Deductions, contributions and RL-1 / T4 slips stay with your
  payroll service.
- **Averaging of hours** (s. 53), by collective agreement or by individual
  written agreement: see below.
- **Retention**: records whose retention period is over are destroyed by
  hand, never those under dispute. See below.
- **Colors per employee** (v18.0.1.1.1): each employee's color, set once on the
  employee form (through `bf_color` and its bridge `bf_color_hr`), paints their
  shifts in the schedule calendar, its filter legend and its popover; each user
  can keep their own color for a colleague. Employees with no color keep the
  calendar's usual per-employee colors. The default rule is « Shifts by
  employee »; a fallback swatch gives every new employee a distinct color.
- **Daylight saving time**: durations are measured in real elapsed time and
  premium windows on the wall clock. The night the clocks go back (22:00 to
  06:00) pays 9 hours; the night they go forward pays 7.

## Roles

| Group | Can |
|---|---|
| **Shifts / Employee** | See published schedules and call lists, their own shifts, offers and changes; answer offers and changes; request swaps; declare availability and benefit events |
| **Shifts / Manager** | Everything else: working conditions, templates, schedules, offers, approvals, pay periods, destruction of old records |

Employees get the group explicitly, and their employee record must be linked to
their user. The Shifts tab of the employee form (working conditions,
seniority, hourly rate, payroll number, variable hours, dispute hold) is shown
only to shift managers who are also HR officers: for anyone else, Odoo opens
the employee's public profile, which has none of these fields. A shift manager
without HR rights still runs schedules, offers and pay. Call lists are
**posted**, as collective agreements require: every employee sees each
member's seniority date and counters there. Records are separated by company.

Answers always go through a method that checks who is answering. On what an
employee creates (availability, benefit events, swaps) they write only the
fields of the form, never a state, a tax verdict or an approver, whether in
the values sent, in the defaults of the context or in their own saved
defaults, and the record must still be theirs after each write. Whoever
attaches a receipt, employee or manager, must have uploaded it. Derived
fields (a schedule's state seen from its shifts, a tax verdict, worked hours,
the company) are never written by a caller. The change log is written by the
module only: managers read it, nobody edits or deletes it, and an answer is
recorded once, with its time and who recorded it. A schedule's state changes
only through its buttons, so a published schedule never goes back to draft,
and a shift moves only between draft schedules; the database itself refuses
to delete a shift or a schedule that has a log. Only the retention action
destroys a published schedule and its log, and never one kept for a dispute.
The same holds for the trace of offers and swaps: a started offer, its lines
and a sent swap request are never deleted nor created already advanced (the
lines come from the call list only), and their state and approver move only
through their buttons.
The internal switches the module uses between its own methods cannot be set
by a caller. Refusal and destruction wizards are seen only by whoever opened
them.

## Minimum presence and on-call pay

- **3-hour minimum** (s. 58). A person who comes to the workplace and works
  fewer than 3 consecutive hours is paid at least 3. It applies to call-backs
  and to short regular shifts alike (shifts less than an hour apart make one
  presence). The working conditions can set either exception of s. 58 (work
  that needs several presences a day; work usually done in full within
  3 hours); a shift can be marked as shortened by force majeure.
- **Remote call-back**: handled by telephone or remote access, it does not
  bring the person to the workplace, so s. 58 does not apply to it. The
  working conditions can still grant their call-back minimum.
- The module pays the hours worked (overtime included) plus a top-up to the
  minimum at the usual rate: more than the floor of s. 58, which s. 94 allows.
- **On call at home** is not worked time (s. 57) and the LNT does not require
  paying it. When the working conditions do (an amount per hour on call, or
  per on-call period), the `ONCALL` line carries the amount, marked taxable in
  Québec and federally.

## Averaging of hours (s. 53)

On the working conditions, choose the source (collective agreement, or
individual written agreement), the number of weeks and the day the cycle
starts.

- Overtime is the regular hours of the period (paid leave included, s. 52)
  minus the weekly threshold (40 hours under the LNT) times the number of
  weeks: 37 h + 47 h gives 4 hours.
  Daily thresholds and the seventh day still count day by day.
- It is paid with the pay period that contains the last day of the averaging
  period; until then, hours are paid at the usual rate. Hours already paid at
  the usual rate in an earlier pay period then get their premium only
  (`OTAVG` line).
- Under an individual agreement: at most 4 weeks, and the hours beyond 50 in a
  week are overtime in that week and leave the average.
- Under a collective agreement, the Act sets no longest period; enter the
  limit your legal advice retains (0 = none).
- A weekly threshold above 40 hours is still brought back to 40: averaging is
  the only way to count overtime over more than a week.

## Taxable benefits: what the module applies

| Event | Québec | Federal |
|---|---|---|
| Overtime meal | not taxable if the overtime was requested by the employer, **planned** to last 2 consecutive hours or more, fewer than 3 times in the week, for a reasonable amount; a **reimbursed** meal needs a receipt, a meal **provided** by the employer does not | not taxable up to $23, 2 hours or more, fewer than 3 times in the week, no receipt required |
| Taxi home | not taxable on the meal conditions (receipt if reimbursed), when there is no public transit or safety is at risk | **taxable by default**: no published CRA exception covers a taxi after overtime; an exemption can be recorded with its justification |
| Parking | taxable, apart from the exceptions | taxable, apart from the exceptions |
| Uniform, protective clothing | not taxable | not taxable |
| Subsidised meals | not taxable if the employee pays at least the cost of the food and its preparation; otherwise taxable on the difference (RL-1 boxes A and V); "to check" without the cost | same rule |

Sources: Revenu Québec and Canada Revenue Agency guides, Taxation Act s.
37.0.3, legal and tax review of September 2026.

**CNESST.** Insurable earnings follow box A of the RL-1. Everything the
module marks taxable in Québec, and every wage line (premiums, on-call pay,
call-back and 3-hour minimum, holiday indemnity), is insurable; the meals and
taxis that are exempt in Québec are not, whatever their federal treatment.

## Retention

Hours, premiums, indemnities and benefits are supporting documents of the
pay. Keep them **at least 6 years after the end of the year they relate to**
(Tax Administration Act s. 35.1; Income Tax Act 230(4)), which covers the
3 years of the labour standards register (N-1.1, r. 6, s. 2); longer while a
grievance, a complaint or a tax objection is under way. After that, the
private sector privacy act asks to destroy them or anonymise them (P-39.1,
s. 23).

- **Shifts › Configuration › Destroy old records** counts, then destroys,
  what ends before the cut-off: schedules (shifts, change log, warnings,
  offers, swaps), pay periods (lines and exported file), benefit events (and
  their receipts) and dated availability, with their chatter and attachments.
  Nothing is destroyed automatically.
- The cut-off is 6 years by default (never fewer) after the end of the year,
  with a month's margin for hours worked in late December and paid in January.
- **Keep for a dispute**, on a schedule or on an employee, keeps the schedule,
  or everything that concerns the person, until it is unticked. Records that
  belong together stay or go together: a pay period and the shifts it paid, a
  benefit and its shift, a swap and both its schedules. A schedule kept for a
  dispute therefore keeps its pay and its benefits.
- Only shift managers destroy, and only for the companies of their user.
- The time bank balance does not move: the net of the destroyed periods is
  carried over on the employee.
- The module **destroys rather than anonymises**: the regulation on
  anonymisation (A-2.1, r. 0.1) requires a re-identification risk analysis
  under a competent person's supervision, and a register, which no button can
  promise. The employee record itself belongs to HR and is not touched.

## What has not been confirmed

These points should be reviewed by a lawyer or a tax specialist before you rely
on them:

- s. 59.0.1, 3rd paragraph: whether a shift added or a day extended beyond the
  limits with less than 5 days' notice may be refused for the added hours only
  or for the whole day, and whether moving the start of an announced shift
  (same length) may be refused. The module asks an answer for both;
- the federal treatment of a taxi home after overtime (taxable by default);
- a Québec overtime meal whose amount is not reasonable: the module makes the
  whole meal taxable, while a CNESST note reports that Revenu Québec includes
  the excess only;
- whether a remote call-back of fewer than 3 hours gives the minimum of s. 58,
  and whether on-call pay enters the holiday indemnity (s. 62). The module says
  no to both;
- averaging under a collective agreement: whether it may run longer than
  8 weeks without the CNESST's authorisation; whether the CNESST's example
  (37 h + 47 h) owes 4 hours or 2; whether the right to refuse beyond 50 hours
  still holds in a given week. The module owes 4 hours and reads the 50 hours
  on the average;
- whether the hours shift by shift, and not only the totals sent to the payroll
  service, must be kept 6 years.

## Known limits

- A shift that crosses two weeks counts entirely in the week it starts.
- Holiday indemnity eligibility (unauthorised absence the working day before or
  after) is not checked: the line is produced for every employee of the period.
- Holiday indemnity is computed on hours at the usual rate. LNT s. 62 speaks of
  1/20 of the *wages* of the four weeks, which include premiums: add them in
  payroll if your premiums are significant. Under averaging, each of the four
  weeks is still capped at the weekly threshold, which may slightly
  under-count a long week averaged with a short one.
- The 2-hour condition of overtime meals and taxis reads the planned overtime
  for the CRA too, which speaks of hours worked.
- Averaging authorised by the CNESST (s. 53, 1st paragraph) is not a separate
  choice.
- An offer answered after its deadline is refused; the scheduled action then
  records "no answer" and moves on, every 5 minutes.
- Public holidays are the company-wide time off (without a resource) of the
  working calendar, under Employees › Configuration.
- A start time inside the ambiguous hour of the autumn change is read as
  standard time.

## Configuration

1. Give users the **Shifts / Employee** or **Shifts / Manager** group.
2. Under Shifts › Configuration, review the labour-standards record, add a
   collective agreement if needed (premiums, call-back and presence minimums
   and their exception, on-call pay, averaging), and create shift templates.
3. On each employee (Shifts tab, as a shift manager with HR officer rights):
   working conditions, seniority date, usual hourly rate, payroll number, and
   "Variable or non-continuous hours" when it applies. The usual day comes from
   the employee's working calendar.
4. Optionally, create call lists and add public holidays to the working
   calendar.
5. Once a year, run Shifts › Configuration › Destroy old records, after
   ticking "Keep for a dispute" where a dispute is under way.

## Requirements

Odoo 18.0 Community. Depends on core modules `hr`, `mail`, `resource`, and on
`bf_color` (Symbifox, LGPL-3) for the colors. Install its bridge `bf_color_hr`
to give each employee a color.

## Tests

`tests/test_engine.py` and `tests/test_engine_rules.py` cover the rules
without the ORM; `tests/test_flows.py`, `tests/test_flows_rules.py`,
`tests/test_notices.py`, `tests/test_retention.py` and `tests/test_couleurs.py`
play the flows under real Employee and Manager accounts, including what an
employee cannot do by calling the server directly.

## Licence

Business Source License 1.1 (see `LICENSE`): free for your own internal use;
providing it to third parties as a product or service needs an agreement. Each
version becomes LGPL-3 on its Change Date.
