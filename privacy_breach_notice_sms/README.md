# Breach notice: never by SMS (`privacy_breach_notice_sms`)

[![Odoo Version](https://img.shields.io/badge/Odoo-18.0-purple.svg)](https://www.odoo.com)
[![License: BUSL-1.1](https://img.shields.io/badge/License-BUSL--1.1-blue.svg)](https://mariadb.com/bsl11/)

Installed automatically where `privacy_breach_notice` and `sms` are both present.

Resending an SMS (`sms.resend`) creates, as superuser, an SMS attached to an existing
message and addressed to a number chosen by the caller: a reader could have sent the
text of a notice's thread from the company's SMS sender. A breach notice goes out by
email to the designated address, and that email is the record. No SMS is attached to a
message of a notice's thread by anyone but the system or an administrator.

## Dependencies

`privacy_breach_notice`, `sms`.

## Licence

Business Source License 1.1, see `LICENSE`.
