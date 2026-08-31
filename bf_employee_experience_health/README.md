# Employee Experience — Allergies (`bf_employee_experience_health`)

Allergies and food allergies on the employee record, readable only by the person
concerned and by whoever organises.

A satellite of `bf_employee_experience`, because it asks exactly the question that
module has already settled: who may read a health detail about a colleague.

## Why this is not just a text field

An allergy is health information, and therefore **sensitive** personal
information under Quebec's Law 25. A field visible to the whole company and a
field reserved to whoever organises meals are not the same feature.

This module reuses the base module's answer: the person concerned and the
administration (`hr.group_hr_user`) read the record, nobody else. The direct
manager has no access.

## What it adds

* An allergen catalogue (`bf.ex.allergen`) with Canada's recognised priority
  allergens loaded on install — twelve food entries, plus three non-food ones
  that are common at work (latex, insect stings, fragranced products).
* One declaration per person (`bf.ex.allergy`): the allergen, the severity, and a
  note. Severity "anaphylaxis" is visible in the list view.
* **A self-service entry point.** Everyone declares their own, from
  *Employee Experience > People > My allergies*, scoped to themselves by the
  record rule.
* A **catering list**: for a group of people, the dietary constraints **without
  the names**. This is what you hand to a caterer, and it avoids circulating a
  medical record to order sandwiches. Non-food allergens are excluded from it.

## The catering list is restricted, and that is deliberate

🔴 `catering_constraints()` is a public `@api.model` method, so it is reachable
over RPC by any internal user — the model grants read to `base.group_user`. It is
therefore gated on `hr.group_hr_user`, and it does **not** run under `sudo()`.

Both halves matter. Without the gate, anyone could call
`catering_constraints(employee_ids=[<one colleague>])`: a group of one is not
anonymous, it is a name, and the caller would get that person's allergen and
severity — the very declaration the record rule forbids them to read. Without
dropping the `sudo()`, that gate would be the only barrier; with it dropped, the
record rule remains a second one.

A menu hidden by a group is not a barrier. The server action behind it stays
reachable.

## What it does not do

It does not replace an emergency plan and does not claim to be a medical record.
It carries no retention rule: that is `bf_employee_experience_health_privacy`,
which adds express consent and destruction on departure.

## People declare their own, and that is not a detail

The privacy bridge sets `requires_express_opt_in` and states that declaring an
allergy is voluntary. A consent its holder cannot exercise is not one, so the
self-service menu is part of the design rather than a convenience: without it,
the only way an allergy reaches the system is HR typing it on someone's behalf,
which is the opposite of what the bridge declares.

⚠️ The employee record's `ex_allergy_ids` and `ex_has_anaphylaxis` stay reserved
to `hr.group_hr_user`, and must. Opening them to staff would expose no allergy —
the record rule empties the list on a colleague — but `ex_has_anaphylaxis` would
then compute "no" for everyone. A safety flag that answers "no" for want of read
access is worse than an absent one.

## Tests

19 tests. The access rule carries a **counter-proof**: its domain is replaced by
`[(1, '=', 1)]` and the test verifies that the colleague's allergy then appears —
without it, a green test would not say whether that rule is what masks. Four more
tests pin the catering list's authorisation rather than only the shape of its
output.

```
odoo -d <database> -u bf_employee_experience_health --test-enable \
     --test-tags /bf_employee_experience_health --stop-after-init --http-port=8180
```

## Licence

LGPL-3.
