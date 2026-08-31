# Employee Experience — Dashboard bridge (`bf_employee_experience_dashboard`)

Auto-installs when `bf_employee_experience` and `bf_home` are both present.
Adds one tile that says three things:

* the catalogue's average take-up rate;
* the number of paid benefits that **nobody** claims, in red, because it is the
  only figure in the module you act on immediately;
* the annual cost of the catalogue.

## Tests

3 tests.

```
odoo -d <database> -u bf_employee_experience_dashboard --test-enable \
     --test-tags /bf_employee_experience_dashboard --stop-after-init --http-port=8180
```

## Licence

LGPL-3.
