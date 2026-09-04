# Recruitment: spend and cost per hire (`bf_recruitment_expense`)

`auto_install` bridge between `bf_recruitment`, `hr_expense` and
`hr_hourly_cost`. It answers the question management asks and that none of the
three can settle alone: **what a hire cost**.

No new model. Three fields on the job, one on the expense, one on the company,
and computations that are not stored.

## The figure, and what it refuses to do

A cost per hire has two halves: the **out-of-pocket** spend (the advert, the
agency, the travel) and **people's time**. The first is easy; the second is
where tools lie.

They lie because they count zero for what they cannot price. A job on which
three people ran six interviews then shows the price of the advert, and nothing
else, wearing the look of a total.

This module never does that:

| What it measures | Where it comes from |
|---|---|
| Out-of-pocket spend | Expenses attached to the job, refused ones excluded |
| Panel person-hours | `bf.interview.duration` × `interviewer_ids`, **held** sessions only |
| **Unpriced** hours | The ones no rate covers: named, not counted as zero |
| Cost per hire | (spend + time) ÷ `no_of_hired_employee` |

🔴 **`cost_is_partial` and `cost_warning` are the reason this module exists.** As
soon as one panel hour has no rate, or no hire has taken place, the job says so
in plain words. The figure is still shown, but it stops passing itself off as
complete.

## Panel time was already being measured

No timesheets are needed: the interview book carries `duration` and
`interviewer_ids`. The product of the two gives person-hours, without a single
extra entry. What was missing was not the measurement, it was a **rate**.

### Where the rate comes from

1. **The employee's hourly cost** (`hr.employee.hourly_cost`, from the core
   `hr_hourly_cost` module), looked up **in the job's company**.
2. **The company's fallback rate** (Recruitment > Configuration), when the person
   has no employee record here or their rate there is zero.
3. **Nothing**, and the hour is then counted as unpriced.

⚠️ **A panel member is not necessarily an employee.** The panel is made of
`res.users`: an administrator, a consultant or a board member can sit without an
employee record. The fallback exists for them.

⚠️ **An employee of ANOTHER company in the group does not lend their rate.**
Their hourly cost belongs to their employer, not to the company doing the
hiring. They fall back.

⚠️ **An hourly cost of zero is not a free employee**, it is a rate nobody set.
The module falls back rather than adding zero to the total.

## Privacy: panel cost is salary data

🔴 Core reserves `hourly_cost` to `hr.group_hr_user`. **Every field derived from
it carries the same restriction here**: `panel_cost`, `recruitment_cost_total`
and `cost_per_hire`. Without that, the module would deliver by arithmetic what
core protects: two people on the panel, a duration known to the quarter hour,
and the rate follows.

The **hours** stay visible to recruitment: they say nothing about a salary. And
`cost_warning` is written **in hours, never in money**, precisely because it
addresses people who are not entitled to see the amounts.

## Nothing is stored, and that is deliberate

None of the computations is a stored field. A stored total would have to
recompute on every rating submitted, every expense approved, every rate change.

**Accepted trade-off:** these fields display but do not **sort** and do not
**group**. A dashboard ranking jobs by cost would require storing them, and that
is not a step to take lightly.

## Two traps paid for while writing this

🔴 **`hr.expense` has no refusal of its own.** Its `refused` state is **computed**
from the expense sheet: it reads "refused" when the sheet is cancelled. A test
that writes `state` directly proves nothing; it has to go through the sheet.

⚠️ **The expense form carries `employee_id` TWICE**, once for HR managers and
once for everybody else. An `xpath` only takes the first, and the field lands in
the wrong half. The anchor is `analytic_distribution`, which appears once, and
which is exactly what the job feeds.

## The analytic key

`hr.job` receives `analytic.mixin`, on the exact pattern of `mrp_account` on
`mrp.workcenter`. An expense attached to a job inherits its distribution **when
it does not have one yet**.

🔴 **Accepted consequence:** changing the job on an expense that already carries
a distribution does **not** rewrite it. That is the price of never erasing a
human entry, and it beats the opposite.

## What stays outside the figure

⚠️ The recruiter's time **outside sessions** (CV screening, calls, drafting
offers) is measured nowhere and therefore does not count. The module counts what
it knows how to count and does not claim the rest.

## Test evidence

* A suite including the pair that proves the salary restriction discriminates:
  the recruiter is refused, the HR manager is served.
* **Five mutations** placed and removed, each one bringing down exactly the test
  that watches it: the company of the rate, the state of the sessions, the
  salary restriction, refused expenses, and overwriting a distribution.
* **13 view checks** loaded under real accounts (recruiter, panel member, HR
  manager), plus parity between declared columns and the database.
