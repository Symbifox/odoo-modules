# Employee Experience: profile types (`bf_employee_experience_profile`)

Psychometric profile types (four behavioural styles, sixteen types, the Big
Five) on the employee record. Declared, never guessed, and never an assessment.

A satellite of `bf_employee_experience`, **installed as an option**. Nothing in
the family needs it, and a personnel file does perfectly well without one.

## Why a module and not a text field

A behavioural style workshop leaves a result behind, and that result ends up on
paper in a cabinet or in a shared file nobody files. This module gives it a
place, with a written purpose and a destruction date (see the privacy bridge,
`bf_employee_experience_profile_privacy`).

The useful half is the sentence that comes with a type, not the letter: "give
notice before a change, and say what stays the same". That is what a colleague
can act on.

## What it adds

* **A catalogue of frameworks** (`bf.ex.profile.framework`). Three are shipped:
  four behavioural styles (DISC family), sixteen types (the Jungian
  dichotomies) and the Big Five. A client using another instrument creates
  their own, with their own items.
* **Two shapes of result**, because frameworks do not all have one: a single
  named type per person, or a score per dimension. The same catalogue of items
  (`bf.ex.profile.type`) serves both, and the highest dimension of a scored
  result is **computed**, not entered twice.
* **One declaration per person and per framework** (`bf.ex.profile`): the date
  of the sitting, who entered it, and whether the person has seen and accepted
  what is written about them.
* **"Working with someone like this"**: a text carried by each item, read by
  its holder and by their manager.
* **Two self-service doors**: *Employee Experience > People > My profile*, and
  *My team's profiles* for a manager.

## Who reads what, and who writes

| | Read | Write |
|---|---|---|
| The person | own | own |
| Their direct manager | their team | no |
| `hr.group_hr_user` | all | all |
| Everybody else | nothing | nothing |

This is a **deliberate departure** from the allergies satellite, where the
manager has nothing: a communication style is useless if the person who has to
communicate cannot see it.

🔴 Two record rules on the same model and the same group combine with **OR**.
The read rule is therefore separate from the write rule, and the write rule
carries `perm_read = False`: otherwise it would widen the read instead of
narrowing the write, and the manager would be able to rewrite their team's
profiles. `tests/test_access.py` mutates each masking rule to `[(1, '=', 1)]`
and requires the leak to appear, so a green test proves the rule is what masks.

🔴 And a measured surprise: in Odoo 18 a user without `hr.group_hr_user`
**cannot open the `hr.employee` form at all** (`RedirectWarning` raised in
`hr.employee.get_views`, towards the public directory). The page grafted onto
the employee record is therefore never the manager's surface; theirs is the
*My team's profiles* action. The test keeps that finding honest: if Odoo ever
changes its mind, it goes red.

## The acceptance is the person's, and nobody else's

`action_acknowledge()` is a public method, so it is reachable over RPC. Its
check is not about a write right, it is about identity: administration holds
every write right on the record and must still be unable to accept on somebody
else's behalf. An acceptance a third party can tick is worth nothing, and
`sudo()` does not lift the check.

The real remedy is stronger than a flag: the person may **delete** their own
declaration, including one administration entered about them.

⚠️ The acceptance is not a gate. A declaration the person has not accepted is
still readable by their manager, and the screen says so in an alert. Making it
a gate is two lines, if that is what the company wants.

## What it refuses to do

* **No questionnaire is administered**, and no instrument is sold or endorsed.
  The module records a result obtained elsewhere. No vendor trademark is in the
  code: each shipped framework is named by what it measures, and "DISC" appears
  only in brackets, as the generic acronym of Marston's model, so a client
  recognises the family their workshop belonged to. A test checks both halves of
  that rule, in both languages.
* **Nothing about a profile is stored on the employee record**, so the staff
  cannot be filtered or grouped by personality type. `headline`,
  `working_with` and `dominant_type_id` are computed and unstored on purpose,
  and a test reads `information_schema` to prove no column exists.
* **Nothing is guessed.** No type is derived from another field, and a missing
  result stays missing.
* **No dominant dimension on a tie.** A freshly prefilled form carries zeros
  everywhere; naming the first dimension "the highest" would be an invention,
  and a manager reads an invention as a fact.

## Profiling, named

The profiling definition of s. 8.1 of Quebec's private sector act covers
evaluating characteristics of a person, "notably for the purpose of analysing
work performance". A profile type falls squarely inside it, and the direct
manager reads the record. The module says so on screen, its purpose says so in
plain language, and the privacy bridge asks for express consent. What a module
can do, it does; forbidding the use remains a company decision, not a line of
code.

## Shipped data

`data/profile_framework_data.xml` is `noupdate="1"`: a client renames their
types and adjusts the texts, and an upgrade has no business overwriting their
work. The price is known: correcting a shipped text takes a migration, not a
`-u`. The sixteen types reproduce no vendor portrait; each text is composed
from the four dichotomies of its code, which is both verifiable and free of
rights.
