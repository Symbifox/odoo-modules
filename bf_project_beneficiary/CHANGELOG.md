# Changelog

## 18.0.1.0.1 (2026-11-28)

The beneficiary takes the same company domain as the ordering customer: on a
multi-company database, a partner reserved to another company is no longer
offered, since the client-facing page of this company would name it. The
search context of the field is documented for what it is, an ordering of the
list, not a filter.

## 18.0.1.0.0 (2026-11-10)

First version. A beneficiary partner on `project.project`, beside the standard
customer relabelled as the ordering customer, plus a computed "beneficiary or
ordering customer" that resolves to the commercial partner. Grouping by
ordering customer and by beneficiary in the search bar, and a menu entry that
opens the projects already grouped by ordering customer.

Born from the same hand-kept project books as `bf_project_number` ("a project
number, an ordering customer, a beneficiary client and a budget"), and from the
mandates where the two parties differ: an association pays for a programme its
member receives, a research centre pays for a training hour given to its own
client. The billing stays with the payer; only the reading changes.
