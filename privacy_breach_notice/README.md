# Breach notice to the controller (`privacy_breach_notice`)

[![Odoo Version](https://img.shields.io/badge/Odoo-18.0-purple.svg)](https://www.odoo.com)
[![License: BUSL-1.1](https://img.shields.io/badge/License-BUSL--1.1-blue.svg)](https://mariadb.com/bsl11/)

An Odoo 18 CE module for **service providers that hold personal information on a
client's behalf** (hosting providers, outsourcers, subcontractors). Under Quebec's
private-sector privacy act (CQLR c. P-39.1, s. 18.3), such a provider must notify the
client's person in charge of the protection of personal information **without delay**
of **any breach or attempted breach** of confidentiality.

The act says neither what the notice must contain nor how to prove it was received.
This module does both.

---

## Two vocabularies, two owners

The provider reports a **breach** or an **attempt** (s. 18.3). The client, as the
organisation responsible for the information, decides whether it is a
**confidentiality incident** (s. 3.6), assesses the risk of serious injury with its
privacy officer (s. 3.7), notifies the Commission and the people concerned (s. 3.5)
and keeps its register (s. 3.8). The notice carries the facts, never the conclusion.

## Three notice types

- **Breach**: unauthorised access, use or disclosure, or loss.
- **Targeted attempt**: aimed at this client's information (an access refused on one
  of its accounts, an exploit attempted against its instance).
- **Periodic report**: attempts blocked on its infrastructure, aggregated by category
  over a period. Internet background noise does not belong in a one-off notice.

## Content

The fields mirror what the controller needs to meet **its own** obligations (CQLR
c. A-2.1, r. 3.1, ss. 3, 5 and 7): nature, circumstances, cause, dates of occurrence
and discovery, information concerned (by category), number of people and how many
reside in Quebec, facts that matter to the assessment (encryption, exfiltration),
measures taken and suggested, authorities outside Quebec, police investigation,
third parties that can reduce the risk. **No information that identifies a person**:
a list of affected accounts travels separately, through a secure transfer.

## Recipient

The notice goes to the **reception address designated** on the client organisation's
record (`privacy_consent`, "Privacy" tab). Without it, the notice is not sent: a legal
notice never goes to a guessed address, not even the designated person's own email or a
general mailbox.
A document is presumed received once it is accessible there (CQLR c. C-1.1, s. 31).

## Proof

- **Sign and send** freezes the notice: signer, time, the PDF and its **SHA-256
  fingerprint**. The stored PDF cannot be rewritten, moved, published or deleted; if it
  ever changed anyway, the fingerprint would show it and the acknowledgement page would
  refuse to serve it.
- A sent notice is never edited: **Prepare an update** creates the next version (same
  number, version + 1), up to the final notice.
- The sent email is kept: it is the record of the dispatch.
- **Acknowledgement.** The email links to a public page that shows the notice and
  acknowledges nothing on its own (mail filters open links by themselves). A link in an
  email is a bearer token that travels in quoted replies, so acknowledging also requires
  a **one-time code** sent to the designated address at that moment: six digits, valid
  thirty minutes, single use, limited attempts and resends. The acknowledgement keeps
  the server time to the second, the fingerprint the person saw, their name and title,
  and says "received", not "agreed".
- **The notice thread is a register.** Only the system (mail gateway, federation,
  acknowledgement page) or someone allowed to edit the notice can write to it, whatever
  the path: posting, the composer, template sending, resending a failed email,
  notifications, attachments, activities, reactions. Once a notice is sent, its messages
  are neither rewritten nor deleted, not even by a manager: add a note that corrects
  them. Notifications only reach the people named on a message and the notice's
  followers.

## Deadlines

"Without delay" is not a number, and the module codes none. An internal deadline is a
matter for the contract between the provider and its client.

## Access

Read: *Privacy / User*. Draft, sign, send: *Privacy / Manager*. The official email
template can only be changed by an administrator.

## Companion modules

Installed automatically when their dependency is present:

| Module | Purpose |
|---|---|
| `privacy_breach_notice_hosting` | Prepare notices from a hosting security event (`hosting_management`) |
| `privacy_breach_notice_mail_tracking` | Keep the tracking of notice emails to the privacy role (`mail_tracking`) |
| `privacy_breach_notice_sms` | A notice never goes out by SMS (`sms`) |

Installed on demand: `bf_federation_privacy`, so that the notice also lands in the register
of a paired client (`bf_federation`).

## Dependencies

`privacy_consent` (which brings `mail` and `portal`).

## Licence

Business Source License 1.1, see `LICENSE`. Each version converts to LGPL-3.0-or-later
on its Change Date.
