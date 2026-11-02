# Document Requests (`bf_document_request`)

Collect the documents you need from a client without chasing attachments by
email. An employee picks the client, ticks the documents to obtain and sends a
link by **email or text message**. The client opens a mobile-first page,
**photographs** each document with their phone's camera (or attaches an image
or a PDF from a computer), and the employee receives the files **encrypted**,
with every step recorded in a **tamper-evident log**.

Built on [Secure Transfer](../bf_securetransfer) (`bf_securetransfer`) for Odoo
18 Community: storage, presigned uploads, quotas, the hash-chained access log,
watermarking and purge on expiry all come from it.

- **Version**: `18.0.1.2.2`.
- **Licence**: **BUSL-1.1**: production use allowed for your own internal
  business operations; providing the module as a product or service to third
  parties (hosted, managed or resold) requires a written agreement. Converts to
  **LGPL-3.0-or-later** on the Change Date stated in [`LICENSE`](LICENSE).
- **Languages**: English source, French (Canada) catalogue included.

## Who it is for

Any organisation that must gather identity or supporting documents from
people who are not Odoo users: onboarding a client, opening a file, renewing a
contract, checking eligibility. The client needs no account and no password:
the link they receive is their only key, and it only opens an upload.

## How it works

1. **Request.** In *Secure transfers › Document requests*, choose the client,
   the documents (from a catalogue you manage), an optional deadline and a
   note shown at the top of the client's page. Send by email or by text
   message (VoIP.ms, through the base module).
2. **Upload.** The client opens `/d/<token>`: one card per document, with its
   instructions. On a phone, *Take a photo* opens the rear camera directly; a
   second button picks a file (an image or a PDF received by email). Files go
   **straight from the browser to the object storage**: Odoo never proxies the
   bytes. A photo can be retaken until the client presses *Finish and send*.
3. **Receive.** On submission the underlying transfer is finalised: each file
   is checked on the storage, and the requester receives the usual secure
   download link from the base module.

## Features

- **Document catalogue** (`bf.document.type`): label, instructions to the
  client, two-sided flag and retention policy. A starter catalogue is
  installed (identity document, proof of address, proof of insurance, proof of
  income) and is yours to change: updates never overwrite it.
- **Per-document retention** (`bf.document.expiry.policy`): when a request mixes
  documents, the **shortest** policy wins, so a document meant to live a day
  never lives a month because another one may. Two starter policies: *Short
  (24 h)* and *Long (30 days)*. A brand can set a default policy.
- **Deadline**: past it, the client's link stops working and a scheduled job
  closes the request. The client's page and the emails show it in the
  requester's time zone, spelled out in the reader's language. A request without a deadline is closed after 30 days
  (`bf_document_request.max_open_days`, 0 = never).
- **Reminders**: manual (*Resend*) or automatic per brand (delay and maximum
  count), by the request's own channel. A reminder only lists what is still
  **missing**. Text message reminders are capped per day
  (`bf_document_request.reminder_daily_cap`) so they can never use up the quota
  a first sending needs.
- **Destination guard**: once a request is sent, only its requester, a secure
  transfer manager or an administrator can change its channel, address, mobile
  number or requester. Any change of destination **rotates the link**: the old
  one dies on the spot.
- **One log, one chain**: the request's lifecycle (sent, page opened, each
  document uploaded, reminders, completion, cancellation, expiry) is written to
  the **same SHA-256 chain** as the transfer's file accesses. The base's access
  certificate (PDF) therefore tells the whole story. Addresses and numbers are
  masked in the log.
- **Readers' languages**: the public page follows the visitor's browser
  (Accept-Language); the email and text messages follow the client's contact;
  log notes and the messages posted by scheduled jobs follow the requester.

## Security model

A request carries a **draft transfer created on the server side**. Its upload
token is never handed to the client's page: the base's finalisation route
accepts a new recipient list on an ordinary brand, so a client holding that
token could redirect their own documents. The request token only opens an
upload, and the destination is fixed on the server when the request is sent.

The link email is attached to no record and authored by nobody on the
employee's side: the link is the key to the upload, and only managers may read
a request's token. State fields (status, transfer, token, dates, reminders) and
a requested document's file and status can only be written by the module's own
actions, never over RPC.

The client receives no receipt by email: the secure transfer would send its
"sender" a receipt carrying the download link of what they uploaded, and here
the sender is the client. Only the requester receives the link; the client's
page confirms the sending. A document the client has uploaded cannot be removed
from its request: its upload is recorded in the log.

## Configuration

- *Secure transfers › Configuration › Document types*: your catalogue.
- *Secure transfers › Configuration › Document expiry*: retention policies (1,
  7 or 30 days, as the brand offers).
- On a brand: default expiry policy, automatic reminders (delay, maximum).
- Text messages require the base module's VoIP.ms settings; without them,
  requests go out by email.

## Uninstalling

The access log is append-only and each entry's action is part of its digest.
On a database that holds document request history, uninstalling is refused
rather than silently breaking the integrity chain.
