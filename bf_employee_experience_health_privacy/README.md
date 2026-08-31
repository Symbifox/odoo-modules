# Employee Experience — Allergies, privacy bridge (`bf_employee_experience_health_privacy`)

Express consent, retention tied to the employment relationship, and destruction on
departure. Auto-installs when `bf_employee_experience_health` and
`privacy_consent` are both present.

An allergy is information concerning health, and therefore **sensitive** personal
information under s. 59 of Quebec's Act respecting the protection of personal
information in the private sector. That is not the same regime as the benefits
usage register, which is why this bridge exists separately from
`bf_employee_experience_privacy`.

## What it changes relative to the benefits bridge

* **Express consent is required.** Declaring an allergy is voluntary: nobody is
  obliged to tell their employer what sends them to hospital. Section 12 para. 2
  requires consent that is manifest, free, enlightened and given for specific
  purposes; s. 59 adds that it be express for sensitive information. The bridge
  therefore sets `requires_express_opt_in`.

* **The duration is the employment relationship, not a clock.** The purpose is
  fulfilled the day the person leaves: no more team meals, no more kit to plan
  for. Section 23 then calls for destruction. A rule expressed in years would be
  false precision.

## 🔴 The purge cron ships DISABLED

It destroys the declarations of people whose departure date has passed, after a
grace period. An irreversible purge must never be a side effect of an install:
somebody has to decide to switch it on, and to choose the delay.

## ⚠️ No aggregate here, unlike usages

The `_health` module can produce an anonymous catering list on demand. Keeping one
after destruction would mean keeping a health statistic about a workforce that no
longer exists, with no decision depending on it. Health data is not retained "just
in case".

## Tests

11 tests.

```
odoo -d <database> -u bf_employee_experience_health_privacy --test-enable \
     --test-tags /bf_employee_experience_health_privacy --stop-after-init \
     --http-port=8180
```

## Licence

LGPL-3.
