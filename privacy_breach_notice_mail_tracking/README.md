# Breach notice and email tracking (`privacy_breach_notice_mail_tracking`)

[![Odoo Version](https://img.shields.io/badge/Odoo-18.0-purple.svg)](https://www.odoo.com)
[![License: BUSL-1.1](https://img.shields.io/badge/License-BUSL--1.1-blue.svg)](https://mariadb.com/bsl11/)

Installed automatically where `privacy_breach_notice` and the OCA module `mail_tracking`
are both present.

`mail_tracking` shows every internal user the subject and the recipient of each tracked
email. For a breach notice, that alone discloses that a notice exists and which client it
concerns. This module keeps the tracking (emails and events) of breach notices and of
incident-register records to the privacy role, within the company of the record. Other
tracking records, orphans included, stay as `mail_tracking` shows them.

## Dependencies

`privacy_breach_notice`, `privacy_incident`, `mail_tracking` (OCA, AGPL-3).

## Licence

Business Source License 1.1, see `LICENSE`.
