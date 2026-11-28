# Changelog

## 18.0.1.0.1 (2026-11-28)

The bridge no longer rewrites the client the page names when no beneficiary is
set. The value was the same in production, but rewriting it erased what another
bridge, or a test of `bf_stepbystep_clients` standing in for one, had put
there: the access code then went to the payer instead of the reader. Access-code
tests now run on the real beneficiary field.

Changing who reads the page now forgets the mandate's access codes and
recognised devices, as the "forget devices" button does: naming, changing or
removing the beneficiary, or changing the ordering customer when no beneficiary
is named. A device recognised before the change no longer opens a page that
names someone else. Moving between two contacts of a company that has its own
address forgets nothing; under a company without one, the contact is who
receives the code, and changing it does forget.

A beneficiary entered as a contact receives the code at their own address when
their company has none, instead of being left out, including when the ordering
customer is another contact of that same company. The fallback stays with the
beneficiary, skips an archived contact, and never reaches a payer from another
company.

## 18.0.1.0.0 (2026-11-10)

First version. The progress page of `bf_stepbystep_clients` names the
beneficiary carried by `bf_project_beneficiary`, and speaks its language, when
the mandate is paid by someone else. Falls back to the ordering customer on
both counts, so a tenant without beneficiaries reads exactly as before.
