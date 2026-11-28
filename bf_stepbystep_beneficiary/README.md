# Beneficiary on the client page (`bf_stepbystep_beneficiary`)

A bridge between `bf_stepbystep_clients` and `bf_project_beneficiary`.

## Why

The progress page names the project's customer. That customer is the party that
orders and pays, and on a mandate paid by a third party it is not the party
reading the page: an association pays for a programme one of its members receives,
a research centre pays for a training hour given to its own client. The reader
was being told the name of a company it never dealt with.

## What it does

Two values on the page:

- **The client the page names** becomes the beneficiary of the mandate, falling
  back to the ordering customer when none is named.
- **The language of the page** follows the beneficiary, falling back to the
  ordering customer when the beneficiary has none.

Two consequences for the access code, when the mandate requires one:

- **The code goes to the beneficiary.** A beneficiary entered as a contact
  receives it at their own address when their company has none. It never falls
  back to the ordering customer: without an address on the beneficiary's side,
  the page says so and sends nothing.
- **Changing who reads the page forgets the mandate's codes and recognised
  devices**, as the "forget devices" button does, so that a device recognised
  before the change does not open a page that now names someone else. That is
  the beneficiary, or the ordering customer when none is named; under a company
  without an address, the contact who receives the code. Moving between two
  contacts of a company that has its own address forgets nothing. Re-attaching
  a contact to another company, on the partner form, does not go through the
  project: use the "forget devices" button then.

It inherits the controller instead of rewriting it: the routes, the guards and
the headers stay with `bf_stepbystep_clients`, and a correction there holds
here.

## Why a bridge and not a dependency

`bf_stepbystep_clients` is installed on tenants that have no use for the
distinction, and a plain dependency would have carried the beneficiary field
and the "ordering customer" relabel onto their projects.

## Install

`auto_install` puts this bridge in place when the second of the two modules
is installed, and never where only one of them is. It does **not** fire on a
database that already has both: Odoo only auto-installs a module during the
pass that installs one of its dependencies, and an update (`-u`) never takes
that path. On such a database, install the bridge by name
(`-i bf_stepbystep_beneficiary`); it otherwise stays uninstalled without a
single error line.
