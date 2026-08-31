# Employee Experience — Daily digest bridge (`bf_employee_experience_digest`)

Auto-installs when `bf_employee_experience` and `daily_todo_digest` are both
present. Injects a "Benefits" section into the daily digest:

* submitted claims awaiting a decision;
* usages recorded with no open entitlement, which are either a data-entry error
  or an eligibility rule to revisit;
* paid benefits nobody has claimed in a year.

The section only appears when there is something to do. Quiet days stay quiet.

## Tests

7 tests.

```
odoo -d <database> -u bf_employee_experience_digest --test-enable \
     --test-tags /bf_employee_experience_digest --stop-after-init --http-port=8180
```

## Licence

LGPL-3.
