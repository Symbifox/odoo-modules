# Hosting Status Page

A status page per hosted client, opened by a private link, without an account.

The client sees, for each of its active services:

- the service name, its address, its software and version, and its environment
  when it is not production;
- its status: **Operational**, **Error** (the service answered with an HTTP
  error), **Unreachable** (it did not answer), **Maintenance in progress**,
  **Not monitored** (no probe watches it) or **Status unknown** (the last check
  is more than 15 minutes old);
- its availability over 30 days.

Then the announced maintenance windows that touch the client (upcoming, in
progress, and those of the last 30 days, with only the client's own services), the
updates applied over the last 30 days, and the time of the last check.

A status turns red only after the same number of consecutive failed checks as
the outage alert (`hosting.health_alert_min_consecutive`, 2 by default): a
single failed check, such as a container restart, does not show.

## What the page never shows

Internal codes, servers, containers, notes, the probe's error message and HTTP
code, and other clients' services. The page is built from a whitelist of values;
no record is handed to the template.

This module shows no security advisory. A module that adds a section to the page
overrides `_hosting_status_completer(values, services)`: it receives the page's
values and its services, in the same order as the service rows, and adds strings
and numbers only.

## Publishing

On the client company's form, the **Status page** tab (Hosting Manager group):

- **Publish** creates the address;
- **Copy the address** shows it, ready to paste in an email;
- **New address** revokes the old one;
- **Unpublish** closes the page.

The page belongs to the client company and lists the services attached to the
company itself. Services attached to one of its contacts, or to a subsidiary, do
not appear: moving a contact under another company is a contact-management right,
and it must not move services onto another client's page. A contact cannot have
its own page.

## Security

- A token of its own (`secrets.token_urlsafe(32)`), compared in constant time;
  never the partner's signup token nor another model's access token.
- Every closed door answers the same 404: unknown client, contact instead of
  company, archived, unpublished, wrong or malformed token.
- `Referrer-Policy: no-referrer`, `X-Robots-Tag: noindex, nofollow`,
  `Cache-Control: no-store`.
- No portal access right on any hosting model: the controller is the only door.
  The methods that build the page are private (`_` prefix), so they cannot be
  called through RPC.
- Every node added to the contact form carries the group: a user without it
  still opens contact forms.

## Known limits

- Merging contacts (the merge wizard) moves the services of the merged record to
  the surviving one in SQL. Since `hosting_management` 18.0.2.63.2, a merge that
  would move hosting services to another client is refused to anyone outside the
  Hosting Manager group.
- A hosting user (not manager) can attach a service to a published client: that
  is the role.

## Requirements

- `hosting_management` 18.0.2.63.1 or later (the maintenance windows), and Odoo's
  `portal` module.

## Licence

Business Source License 1.1. Each version converts to LGPL-3.0-or-later on its
Change Date; for this version, 2030-10-21. See [`LICENSE`](LICENSE).

## Version history

- **18.0.1.2.0**: extension point `_hosting_status_completer(values, services)` for
  modules that add a section to the page. Documentation corrected: services
  attached to a contact do not appear on the company's page.

- **18.0.1.1.0**: announced maintenance windows (`hosting.maintenance.window`,
  `hosting_management` 18.0.2.63.1): upcoming and in-progress windows, then those of
  the last 30 days, each listing only the client's own services; "Maintenance in
  progress" on the services a window covers while it runs.
- **18.0.1.0.0**: first version. Services, versions, smoothed status with its
  time, 30-day availability, updates applied.
