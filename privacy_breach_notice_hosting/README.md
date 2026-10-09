# Breach notices from hosting security events (`privacy_breach_notice_hosting`)

[![Odoo Version](https://img.shields.io/badge/Odoo-18.0-purple.svg)](https://www.odoo.com)
[![License: BUSL-1.1](https://img.shields.io/badge/License-BUSL--1.1-blue.svg)](https://mariadb.com/bsl11/)

Installed automatically where `privacy_breach_notice` and `hosting_management` are both
present.

A hosting security event gains a **privacy assessment**: does it touch personal
information held for a client? The answer is documented both ways (a "no" needs its
rationale too), the affected organisations are proposed from the services involved, and
**Prepare breach notices** creates one draft notice per affected organisation, pre-filled
with the event's circumstances and resolution. The notices themselves are then reviewed,
signed and sent from `privacy_breach_notice`.

The notices linked to an event are visible to the privacy role only; a hosting technician
without that role reads the event without them.

## Dependencies

`privacy_breach_notice`, `hosting_management`.

## Licence

Business Source License 1.1, see `LICENSE`.
