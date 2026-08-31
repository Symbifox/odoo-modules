# Employee Experience (`bf_employee_experience`)

The benefits catalogue: who is entitled to what, who actually uses it, and what
it costs.

## The problem

Odoo knows who works here. It does not know what that person is entitled to,
whether they use it, or what it costs.

The two Odoo tools that would serve retention best are out of reach on Community:
`hr_appraisal` is `uninstallable` under OEEL-1, and the salary-package configurator
is not in the image. This module does not replace them. It covers the benefit,
its eligibility, its usage and its cost.

One observation shaped the whole design: the catalogue mechanics already exist
elsewhere, on the wrong axis. `bf.gamification.reward` carries a "Benefit"
category and a full claim cycle, but it unlocks on XP that is **earned**. A
benefit is granted because someone is **entitled** to it. The two can coexist;
they do not replace each other.

## The five models

| Model | What it carries |
|---|---|
| `bf.ex.benefit` | the benefit: category, supplier, validity, one of three cost models |
| `bf.ex.eligibility.rule` | entitlement expressed as readable criteria, never as a raw domain |
| `bf.ex.entitlement` | the resolved right, computed by rule or granted by hand |
| `bf.ex.usage` | who used it, when, at what real cost |
| `bf.ex.claim` | the request, for benefits that require approval |

## Design rules

**A rule is an AND; the rules of one benefit are an OR.** A criterion left empty
constrains nothing. To cover two employment types, write two rules.

**Criteria are ticked, never written as an Odoo domain.** A domain is more
powerful and cannot be explained. Someone contesting their entitlement must be
able to hear why they do not have it, and `criteria_summary` states the rule in
one sentence.

**The rule computes the right; it does not replace it.** A right can be granted
by hand, with a mandatory written reason and the name of whoever granted it. The
cron never touches a manual right: the exception negotiated at hiring is exactly
what it must not undo.

**A lost right is closed, not deleted.** Without an end date, "what was she
entitled to last March" has no answer. A closure dated today still covers today.

**A confirmed usage line is frozen.** It belongs to the date it happened. The
note stays editable; the figures do not.

**The gateway to a claim is the entitlement**, not a points balance. With no open
right on the day of the request, it does not go through.

**A usage without a right is not blocked, it is flagged.** Blocking it would hide
the anomaly; flagging puts it in front of whoever administers the plan.

## Seniority requires `hr_contract`

⚠️ `hr.employee` carries `departure_date` and `departure_reason_id` but **no
arrival date**. `first_contract_date` belongs to `hr_contract` (LGPL-3, and so is
its whole dependency chain). Without it, neither seniority, nor median seniority,
nor turnover can be computed.

A person with no contract therefore has no known seniority and passes no
seniority criterion: an unknown seniority does not open a right.

## Who reads what

The usage register says things about a person's health: employee assistance
programme, insurance, sick leave. It is read by **the person concerned and by the
benefits administration** (`hr.group_hr_user`), never by the direct manager nor by
the rest of the company.

The catalogue itself is visible in full to all staff, with each person's
eligibility shown. "After a year you will be entitled to it" cannot be said if the
benefit is hidden.

No new groups: the two from `hr` already describe the roles.

## The six indicators

Take-up rate per benefit, cost per benefit, cost per person, median seniority,
turnover rate, and the list of benefits nobody uses.

`bf.ex.indicator` stores one reading **per month and per company**. A turnover
rate is not a number, it is a number over a period.

⚠️ The cost of a departure carries a **method**, not a sum. Real components and a
percentage of annual salary estimate the same thing; adding them would count the
same departure twice.

## The starter catalogue

The module would install onto an empty screen. `_load_starter_catalogue()` creates
ten benefits that are common in Quebec, with their rules: group insurance after
three months, group RRSP after a year, assistance programme, floating days, remote
work, training budget, mobile phone, transit, fitness, and family-obligation leave.

It loads from the menu **Catalogue > Load a starter catalogue**, and automatically
in a demo database. Same method in both cases: one list to keep up to date. The
call is idempotent and never overwrites a benefit that is already present, even a
modified one.

The starter rules use only criteria available everywhere: seniority, employment
type, or nothing. None can depend on a department or a job position that might not
exist at a given installation.

## Bulk import is already there

"Bulk CSV import" needs no code: Odoo's native importer works on any model. To
load a usage history, first export three `bf.ex.usage` lines to get the headers,
then re-import. Lines arrive as drafts; they are confirmed in bulk afterwards.

## The satellites

| Module | What it adds |
|---|---|
| `bf_employee_experience_expense` | an approved expense becomes a confirmed usage, at real cost. Auto-installs with `hr_expense` |
| `bf_employee_experience_dashboard` | take-up tile and a count of benefits nobody claims. Auto-installs with `bf_dashboard` |
| `bf_employee_experience_digest` | a "Benefits" section in the daily digest. Auto-installs with `daily_todo_digest` |
| `bf_employee_experience_health` | allergies and food allergies, with an anonymous catering list |
| `bf_employee_experience_privacy` | purpose, retention rule, and the anonymised aggregate that survives destruction. Auto-installs with `privacy_consent` |
| `bf_employee_experience_health_privacy` | a separate regime for allergies: express consent, destruction on departure |

## Destroying without losing the measurement

The usage register is what lets you say "we pay for this and nobody takes it".
Destroyed at term, it cannot be reconstituted, and the company loses the
measurement along with the personal data. Yet it only needs the measurement.

`bf_employee_experience_privacy` keeps, per benefit and per year, the number of
distinct people, the number of usages, the cost and the take-up rate. No names.
And it enforces the order: a campaign targeting a usage line whose year has not
been aggregated **raises**, destroying nothing and certifying nothing.

## What it does not do

* No measurement of lived experience. An eNPS belongs in a separate module: it
  requires anonymous collection and a threshold of respondents per segment, which
  is the opposite of the named register this module keeps. Mixing the two in one
  model is how an "anonymous" survey ends up joinable back to a person.

## Tests

111 tests across the family, 57 of them on the base module. Fresh install from an
empty database, views loaded under a real account for each role (never uid 1,
which has no groups), and parity between declared columns and
`information_schema` with zero missing.

Every access rule that masks carries a **counter-proof**: its domain is replaced
by `[(1, '=', 1)]` and the test verifies that the leak appears. Without it, a
green test would not say whether that rule is what masks.

```
odoo -d <database> -u bf_employee_experience --test-enable \
     --test-tags /bf_employee_experience --stop-after-init --http-port=8180
```

## Licence

LGPL-3.
