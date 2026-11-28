# Project Beneficiary (`bf_project_beneficiary`)

Who pays and who receives, told apart on the project.

## Why

A project carries one customer in Odoo, and the whole billing chain descends
from it: the analytic lines, the invoices, the hour banks all follow
`partner_id`. Hand-kept project books name two parties instead of one, because
the two are often different companies: an association pays for a mandate that
one of its members receives, a research centre pays for a training hour given
to one of its clients.

Put the beneficiary in the customer field and the invoice goes to the wrong
company. Put the payer there and the client-facing pages name a company the
reader has never dealt with. Both parties need a field of their own.

## What it does

- **The standard customer becomes the ordering customer**, relabelled on the
  form, in the list and in the search bar. Nothing moves: it is still the party
  the invoices and the analytic lines follow.
- **The beneficiary reads beside it**, as a partner of its own. Leave it empty
  and the ordering customer *is* the beneficiary, which is the case for most
  projects: the field carries the difference, it is not filled twice.
- **A computed "beneficiary or ordering customer"** gives the party a
  client-facing page must name, resolved to the commercial partner (a project
  attached to a contact names the company). Read it rather than redoing the
  "otherwise the ordering customer" rule in every reader.
- **Grouping**: the search bar groups by ordering customer or by beneficiary
  (Odoo 18 offers neither on projects), and a menu entry under Project opens
  the list already grouped by ordering customer.

## What it does not do

- **It does not move the billing.** Invoices, analytic lines and hour banks
  keep following the ordering customer. A timesheet on a mandate paid by an
  association still bills the association.
- **It does not refuse a beneficiary equal to the ordering customer.** The
  entry carries no information, but refusing it would get in the way of a bulk
  import; the computed field reads the same either way.
- **It does not touch the project name.** A name that carries the client or the
  tier of the offer stays as it is, for internal tracking.

## Install

Depends on `project` alone. Nothing to configure: the field is empty everywhere
after the install, and projects read exactly as before until one is filled.
