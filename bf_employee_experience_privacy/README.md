# Employee Experience — privacy bridge (`bf_employee_experience_privacy`)

Declares what the usage register collects, sets a retention rule, and keeps the
measurement when the lines go away. Auto-installs when `bf_employee_experience`
and `privacy_consent` are both present.

The base module knows who is entitled to what and who uses it. It does not know
how long it is allowed to know. This bridge tells it.

## What it declares

* A purpose, "Benefits administration". With no consent to ask for:
  administering a benefit provided for in the terms of employment is performance
  of the employment relationship. Asking for consent would suggest it could be
  refused without the benefit stopping, which is untrue.
* A retention rule, RH-EX-1, aligned with RH-001 "Employee records": 3 years
  active, 2 semi-active — one regime for the whole file. Two durations on the
  same person would force somebody to work out, for every item, which one
  applies.
* The three models that carry personal information become classifiable: the
  entitlement, the usage and the claim.

## The aggregate, written BEFORE destruction

The usage register is what lets you say "we pay for this and nobody takes it".
Destroyed, it cannot be reconstituted, and the company loses the measurement along
with the personal data. Yet it only needs the measurement.

`bf.ex.usage.aggregate` keeps, per benefit and per year, the number of distinct
people, the number of usages, the cost, and the take-up rate. **No names, no
person identifiers.** The aggregate survives the destruction of the lines.

🔴 And the bridge enforces the order: a campaign attempting to destroy a usage line
whose year has not yet been aggregated **raises**. It destroys nothing, and it
records nothing in the register. That is the only order that works: aggregate
first, destroy second.

## Attachments

⚠️ `mail.thread.unlink` removes messages and followers, not the attachments linked
directly to the record. A receipt dropped on a usage line would therefore survive
the destruction of that line. The bridge deletes them explicitly.

## Tests

13 tests, including the one that proves a campaign raises rather than destroying
when the aggregate is missing.

```
odoo -d <database> -u bf_employee_experience_privacy --test-enable \
     --test-tags /bf_employee_experience_privacy --stop-after-init --http-port=8180
```

## Licence

LGPL-3.
