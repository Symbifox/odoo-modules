# Federation: breach notices (`bf_federation_privacy`)

[![Odoo Version](https://img.shields.io/badge/Odoo-18.0-purple.svg)](https://www.odoo.com)
[![License: BUSL-1.1](https://img.shields.io/badge/License-BUSL--1.1-blue.svg)](https://mariadb.com/bsl11/)

When a service provider's client also runs Symbifox and the two instances are paired
with `bf_federation`, a breach notice (`privacy_breach_notice`) does not only go out by
email: **it lands in the client's confidentiality-incident register**
(`privacy_incident`).

---

## On the provider's side

On sending, if a paired peer represents the responsible organisation and announces that
it accepts the `breach` kind, the notice is also shared with it. Only that
organisation's peer can receive it, never another client. The email goes out anyway.

Sharing a notice requires the *Privacy / Manager* role, not the project-manager role.
Links of breach notices are set by the system alone, when a notice is shared or
received, and are visible to the privacy role within the notice's own company.

## On the client's side

The notice lands twice, on purpose:

- a **mirror** of the notice as received, with the PDF and its fingerprint
  **recomputed from the bytes**. A declared fingerprint that does not match the PDF is
  refused. The mirror cannot be edited: the provider sends the next version;
- a **"Declared" record** in the incident register, pre-filled with the facts, the
  awareness date set to the time of receipt, the risk assessment left to the client.
  An update of the notice follows the same record without touching what the client
  wrote. A reception activity goes to the designated privacy officer, or to a member of
  the privacy role in that company.

Two providers may use the same numbering: a number is unique among our own notices, and
among those of a given provider. The provider shown is the authenticated paired peer,
not the name the other instance declares.

## Acknowledgement

The designated privacy officer (or a member of the group that holds that function)
clicks **Acknowledge receipt**. The acknowledgement goes back with their name, title and
the fingerprint of the version they read; **the time kept is the time of receipt on the
provider's side**, never the time the peer declares. It is only accepted from the peer
the notice was shared with, and only for a notice whose PDF actually travelled.

## Messages

A breach notice relays no message in either direction: its thread is a register of
evidence, not a conversation. The notice, its versions and the acknowledgement travel as
their own kinds (`breach.share`, `breach.ack`). There is no `breach.card`: a sent notice
never changes, each version is a new share. With `bf_federation_discuss`, the companion
`bf_federation_privacy_discuss` makes sure no discussion channel is attached to a notice.

## Dependencies

`bf_federation`, `privacy_breach_notice`, `privacy_incident`.

## Licence

Business Source License 1.1, see `LICENSE`. Each version converts to LGPL-3.0-or-later
on its Change Date.
