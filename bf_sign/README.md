# Blue Fox — Electronic signature (`bf_sign`)

**Native Odoo 18 Community** electronic signature (with no dependency on Odoo
Enterprise's `sign` module), instrumented to **hold up under Quebec law**. A
provable, tamper-evident simple electronic signature (SES), with an optional
**PAdES digital seal** (a "signed / not altered" document in a PDF reader, the
way DocuSeal does it).

- **Version**: `18.0.3.13.2` — see [`CHANGELOG.md`](CHANGELOG.md).
- **Licence**: **BUSL-1.1**. Production use is allowed for your **own internal
  business operations**; providing the module as a product or service to third
  parties (hosted, managed or resold) requires a written agreement. Converts to
  **LGPL-3.0-or-later** on **2029-07-20**. See [`LICENSE`](LICENSE). It does not
  cover embedded third-party works (typefaces): see
  [`THIRD-PARTY.md`](THIRD-PARTY.md).
- **Threat model & non-guarantees**: see [`SECURITY.md`](SECURITY.md).

---

## Overview

`bf_sign` lets you have a PDF signed **from inside Odoo** by one or more
signers, through a **personal tokenised public link** (no Odoo account
required), then produces a **sealed document plus a completion certificate**
with a **tamper-evident audit trail**. The goal is a signature that "holds up in
court": explicit consent, a signer↔document link, verifiable integrity, and a
presumption of integrity that shifts the burden of proof.

Beyond manually uploading a PDF, any other module (Sales, Purchasing, …) can
**send its own document for signature** through the `bf.sign.mixin` mixin; the
signed document is then **posted back into the source record's thread**.

## Legal framework (Quebec / Canada)

- Electronic signature is valid in principle: **art. 2827 C.C.Q.** (the
  signature manifests consent, handwriting is not required) and the *Act to
  establish a legal framework for information technology* (**LCCJTI**), the
  functional equivalence principle.
- Three conditions for enforceability: **consent**, a **signer↔document link**
  (LCCJTI s. 39), and **verifiable integrity** (LCCJTI s. 5-6).
- **Presumption of integrity** (**art. 2840 C.C.Q.**): it is the party
  contesting the document who must prove the breach — the burden is reversed.
- Case law: *Bennington Financial Corp. c. Dufour* (Court of Québec) — a
  DocuSign signature was recognised on the strength of a completion
  certificate, an audit trail and the circumstances surrounding the signature.

> **Scope.** This module produces a **simple electronic signature (SES)**, not
> an advanced (AES) or qualified (QES) signature in the eIDAS sense. There is no
> QES regime in Canada, and notarial acts remain out of scope. The PAdES seal
> strengthens the final document's **tamper evidence** but remains a self-signed
> organisational seal (see "Digital seal" below). The `signature_method` field
> offers only `native_ses`: no AES tier is implemented, and the module therefore
> does not offer one.

---

## Features

### Signing
- **Multiple signers**, in parallel or in sequence (the next one is chased automatically in sequential mode).
- **Visual field placement** (signature / initials / date / text / name / email / number / checkbox) by drag and drop on the document (OWL widget + PDF.js), with coordinates in page fractions, independent of resolution.
- **A magnetic grid** with edge-alignment guides against neighbouring fields, **separate from showing the grid** (the ruling is off by default — Alt suspends the magnetism entirely), continuous placement, keyboard nudge, duplicate, and a properties bar that assigns the signer *after* the field is dropped.
- **Signer-fillable fields** with a `signer` / `fixed` / `auto` fill mode; `auto` resolves the signing date, the signer's name and their email. Preset values and the required flag are editable on the field itself.
- **A field order you control**: the sequence the signer is presented with is editable, defaulting to reading order.
- **Reusable field templates** (`bf.sign.field.template`) storing the layout **per signer rank**.
- **A branded, responsive public signing page**: a rendered preview of the document with **numbered placement markers** (reading order), a live mirror of the signature and fields, explicit timestamped consent, and the option to **refuse**.

### Signer identity
- **A personal tokenised link** per signer (UUID, constant-time comparison), sent by email — the proof is control of the inbox.
- **Optional code verification (OTP)**: the signer enters a 6-digit code sent to their email **before** viewing and signing (proof of inbox control *at the moment of signing*). Can be enabled globally and/or per request. Time-limited code, capped attempts.
- **The signing link is never exposed to the requester**: `access_token` and `signing_url` are manager-only; a manager can reveal the link through a **"Copy link"** action, which is **written to the audit trail**.

### Integrity & evidence
- **PAdES digital seal** (pyHanko) *(optional)*: an invisible cryptographic "organisation" signature on the final document, so a PDF reader (Adobe and others) shows "signed / not modified".
- **SHA-256 hashes** of the original document, the stamped (timestamped) content and the sealed bundle.
- **A chained append-only audit trail** (hash chain): UTC server timestamp, IP, user agent, identity method (`email_link_token` / `email_otp` / `internal_user`), before/after hashes — `write`/`unlink` blocked at ORM level.
- **A public verification page** any holder of the document can reach, which **recomputes** the proofs at each visit rather than showing a stored verdict. It discloses no email address and never serves the document.
- **A drop zone on that page that checks the holder's own copy**: they pick or drag their PDF and get a plain verdict, instead of being told to run `shasum` themselves. The file is hashed **in the browser** (WebCrypto `crypto.subtle`) against `hash_signed` and is never uploaded — the page takes in no file, so the public route gains no intake to abuse. A mismatch names the legitimate causes (a PDF re-saved by a viewer, a scan of a printout, the certificate delivered separately) before pointing at tampering. Browsers without WebCrypto get the `shasum` / `Get-FileHash` commands instead of a zone that silently does nothing.
- **An optional verification QR stamped on the document itself** (choice of corner and pages), drawn as vector geometry so it survives printing, and **clickable** — the whole card is a link annotation, because on screen nobody wants to scan a code they could click.
- **Structural locking**: recipients and fields frozen once the request is sent (editable only in "draft").
- **RFC 3161 trusted timestamping** *(optional)*: a TSA token over the signed content, **shown in the certificate**, giving independent proof of date.
- **The verification link and a QR on the completion certificate**, so the pointer to the proof travels inside the signed bundle even when the QR on the document pages is off (it is, by default). Embedded as a `data:` URI, so rendering never depends on an HTTP callback into the server.
- **"Share the verification link"** from the Proof tab, which opens a prefilled composer. It refuses to mint a token for a request finalised before the verification page existed, rather than quietly altering a signed record to make a button work.
- **One-click integrity verification**: recomputes the log chain, the sealed document's hash, the PAdES seal and the timestamp token.

### Integration & experience
- **Sending for signature from other modules** through `bf.sign.mixin` (Sales, Purchasing, … — see "Integration"), with the **signed document posted back** into the source record's thread.
- **A branded PDF completion certificate**, merged into the document or **kept as a separate file** (`append_certificate`) — the evidence is identical either way.
- **Automatic reminders** to signers who have not signed, counted from each signer's own invitation, capped and turn-aware, plus a one-shot alert when a signer has never even **opened** the document.
- **Opening tracked on the record** (first/last seen, count) and rolled up on the request as a status shown in the list, so "has this person looked at it" does not mean reading the journal.
- **Branded emails** (invitation, completion with attachments, refusal, OTP code) through `bf_onboarding_base` (company colours/logo) plus `bf_lexend`.
- **Automatic link expiry** (daily cron), a **reminder cron**, and an **onboarding wizard** (`bf_onboarding_base`).

---

## Data model

| Model | Role |
|---|---|
| `bf.sign.request` | The signature request: document, settings, signers, fields, hashes, signed artefacts, `res_model`/`res_id` link to the source record. |
| `bf.sign.signer` | A signer: email, `access_token` (manager-only), state, signature/initials image, OTP fields. |
| `bf.sign.field` | A placed field (type, page, fractional position, fill mode, preset value, presentation order). |
| `bf.sign.field.template` (+ `.line`) | A reusable field layout, per signer rank. |
| `bf.sign.log` | The chained append-only log (immutable). |
| `bf.sign.seal` (AbstractModel) | The PAdES sealing layer (certificate generation, `seal_pdf`, `verify_pdf`, Fernet key handling). |
| `bf.sign.mixin` (AbstractModel) | "Send for signature" on any model. |

---

## Integration: sending a document from another module for signature

The module exposes a factory and a mixin to wire signing into any Odoo model
that has a PDF report.

**Factory** — `bf.sign.request.create_from_record(record, report_ref=…,
document_file=…, signers=…, field_template=…, send=False)`: renders the record
as a PDF (through `ir.actions.report._render_qweb_pdf`) or accepts PDF bytes,
creates the **linked** request (`res_model`/`res_id`), adds the signers, applies
a field template where relevant, and sends if asked to.

**Mixin** — `bf.sign.mixin` adds to a model:
- the **"Send for signature"** button (`action_send_for_signature`) — creates a
  linked draft request and opens it to place the fields and then send;
- a **"Signatures" smart button** (`action_view_sign_requests`);
- customisation points: `_sign_report_ref()`, `_sign_default_signers()`
  (the record's `partner_id` by default), `_sign_document_filename()`.

**Posting back** — on full signature, `_notify_source_signed()` copies the
signed PDF plus the certificate onto the source record and posts a note in the
thread (no state change).

**Bridge modules provided** (install as needed, each with its own
dependencies):

| Module | Target | Report |
|---|---|---|
| `bf_sign_sale` | `sale.order` (quotations / orders) | `sale.action_report_saleorder` |
| `bf_sign_purchase` | `purchase.order` | `purchase.action_report_purchase_order` |
| `bf_sign_account` | `account.move` (customer invoices / vendor bills) | `account.account_invoices` |
| `bf_sign_privacy` | `privacy.consent` (Law 25 consents) | `privacy_consent.action_report_consent_certificate` |

Wiring a new model comes down to: `_inherit = ["<model>", "bf.sign.mixin"]`,
overriding `_sign_report_ref()` (return the PDF report's xmlid), and adding the
`action_send_for_signature` / `action_view_sign_requests` buttons to the view.

---

## Dependencies

**Odoo modules**: `mail`, `portal`, `bf_lexend`, `bf_onboarding_base`.

**Python libraries**
- *Required* (shipped with the Odoo image): `Pillow` (PIL), `reportlab`, `PyPDF2`.
- *PAdES seal (optional)*: `cryptography`, `pyHanko` (+ `asn1crypto`,
  `pyhanko-certvalidator`). Without them the seal stays inactive; everything
  else works.
- *RFC 3161 (optional)*: `requests`, `asn1crypto` (imported lazily, only when
  enabled).

---

## Configuration

*Settings → Electronic signature*:

- **Encryption key (sealing)** — the Fernet key protecting the sealing
  certificate. **Generate** a key (stored in the database) or **import** one. A
  key set in `odoo.conf` / the environment always takes **precedence** (see
  "Digital seal" and `SECURITY.md` for the database trade-off).
- **Digital seal (PAdES)** — a **"Generate the sealing certificate"** button
  (self-signed, "<Company> — Signature seal"). The seal becomes active as soon
  as a certificate exists; the checkbox acts as a kill switch.
- **Code verification (OTP)** — enable, by default on every new request,
  verification through a code sent to the signer's email. Adjustable per
  request.
- **Default deadline** (days) before a link expires.
- **Size limits**: caps on signature images (KB) and the uploaded document (MB).
- **Default consent text**.
- **RFC 3161 timestamping**: enable plus the **TSA URL**. Off by default (it
  adds a synchronous network call at finalisation).

### Choosing a timestamping authority (TSA)

- **Notarius** (Quebec) — aligned with the C.C.Q./LCCJTI framework.
- **DigiCert** (`http://timestamp.digicert.com`), **Sectigo**
  (`http://timestamp.sectigo.com`) — recognised commercial authorities.
- `https://freetsa.org/tsr` — free, **for testing only** (its root must be
  distributed in order to verify the tokens).

### Digital seal: setting it up

1. **Fernet key**: set it in `odoo.conf` (`bf_sign_fernet_key = …`) or the
   environment (`BF_SIGN_FERNET_KEY`) — recommended and hardened; **or**
   generate it from Settings (stored in the database, self-service). The key
   encrypts the sealing certificate in `ir.config_parameter`.
2. **Certificate**: click "Generate the sealing certificate". Every finalised
   document is then sealed automatically.
3. Because the certificate is **self-signed**, Adobe shows "valid but not
   trusted by a CA". For the green check, anchor it to a recognised PKI (a
   future tier).

> Changing an already-active Fernet key while a certificate exists is
> **refused** (the certificate would become unreadable); delete the certificate
> first.

---

## How it works (life cycle)

1. **Creating** a request (an uploaded PDF **or** one sent from another module),
   adding the signers, placing the fields.
2. **Sending**: PDF validation, computing `hash_original`, the deadline, and
   sending the personal links (all of them, or the first one in sequential
   mode).
3. **(Optional) OTP verification**: the signer enters the code received by email
   before accessing the document.
4. **Signing**: the signer views the document, consents, signs — or **refuses**.
   Images are validated (PNG, size, integrity) before acceptance.
5. **Finalisation** (last signer): stamping, optional RFC 3161 timestamping,
   certificate rendering, merging, the optional **PAdES seal**, hashes, logging,
   a confirmation email (signed document plus certificate), and **posting back**
   to the source record where relevant.
6. **Verification, later and by anyone**: whoever ends up holding the PDF opens
   the verification page (from the QR on the document, or from the URL on the
   request's Proof tab), sees the proofs recomputed live, and can drop their own
   copy in to confirm it is byte-for-byte the delivered one.

---

## Security model (summary)

- **Token-based access**: a UUID `access_token` per signer, compared in constant
  time (`hmac.compare_digest`), **manager-only** (the requester cannot sign on
  their own behalf); a manager revealing it is **logged**.
- **Email OTP** (optional): proof of inbox control at the moment of signing;
  expiry plus an attempt cap; the `/document` and `/submit` routes are blocked
  until verified.
- **Anti-brute-force**: per-IP rate limiting of token failures (in memory, per
  worker — see `SECURITY.md`).
- **Integrity**: SHA-256 hashes plus a **chained append-only log**; the PAdES
  seal plus **RFC 3161** anchoring (an independent TSA) for off-platform proof.
- **Verifiable by the other side**: the public page recomputes the proofs on
  every visit and lets a holder hash their own copy **in their browser**, so
  the counterparty never has to take our word for it — and never has to hand
  their file to us to find out.
- **Tamper evidence**: a signed document cannot be deleted; log entries can
  neither be modified nor removed.
- **Secrets**: the sealing certificate/key are Fernet-encrypted; the Fernet key
  can stay out of the database (env/`odoo.conf`) — the trade-off is documented
  in `SECURITY.md`.

See **[`SECURITY.md`](SECURITY.md)** for the detailed threat model, the scope of
the public routes and the non-guarantees.

---

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2029-07-20, this version converts automatically to
  **LGPL-3.0-or-later**.

Embedded third-party works (typefaces) remain under their own licences: see
[`THIRD-PARTY.md`](THIRD-PARTY.md).
