# Employee Experience — Dashboard bridge (`bf_employee_experience_dashboard`)

Auto-installs when `bf_employee_experience` and `bf_home` are both present.
Adds one tile that says three things:

* the catalogue's average take-up rate;
* the number of paid benefits that **nobody** claims, in red, because it is the
  only figure in the module you act on immediately;
* the annual cost of the catalogue.

The tile is an indicator for the **benefits administration**
(`hr.group_hr_user`), the only role that reads the whole register it summarises
(eligibility rules, every entitlement, every usage). For anyone else it is not
computed at all: no tile, and nothing written to the log. Before 18.0.1.0.2
every home-screen load by an ordinary employee logged an access error on the
eligibility rules, and without that error the figures would have been computed
on that employee's own lines only. The section is computed inside a savepoint,
so a failure there never takes the other home-screen sections down with it.

## Tests

7 tests, the role tests played as an ordinary employee and as the benefits
administration.

```
odoo -d <database> -u bf_employee_experience_dashboard --test-enable \
     --test-tags /bf_employee_experience_dashboard --stop-after-init --http-port=8180
```

## Changelog

### 18.0.1.0.2

- The tile is for the benefits administration only; other users get no tile
  and no access error in the log.
- The section runs in a savepoint and reports a real failure in `failed`, like
  the other home-screen collectors.

## Licence

Business Source License 1.1 — see [LICENSE](LICENSE). You may run it for your
own internal business operations; providing it to third parties as a hosted,
managed or resold service requires a written agreement. This version converts
to LGPL-3.0-or-later on 2030-10-19.
