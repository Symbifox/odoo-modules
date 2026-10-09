# Federation: no channel for a breach notice (`bf_federation_privacy_discuss`)

[![Odoo Version](https://img.shields.io/badge/Odoo-18.0-purple.svg)](https://www.odoo.com)
[![License: BUSL-1.1](https://img.shields.io/badge/License-BUSL--1.1-blue.svg)](https://mariadb.com/bsl11/)

Installed automatically where `bf_federation_discuss` and `bf_federation_privacy` are
both present.

The discussion channel of a federated record admits anyone who can read the record, and
what is written there goes back to the record's thread and on to the peer. For a breach
notice, a mere reader would thus speak to the client on the provider's behalf, in the
very thread that serves as evidence. A notice is not a conversation: it has no channel,
nobody is admitted to one, and no channel can be attached to a notice's link.

## Dependencies

`bf_federation_privacy`, `bf_federation_discuss`.

## Licence

Business Source License 1.1, see `LICENSE`.
