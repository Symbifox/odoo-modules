# Security — `bf_securetransfer`

## Reporting a vulnerability

Please report any vulnerability responsibly to
**security@bluefoxconsultant.com**, without prior public disclosure.

## Scope

The module exposes **unauthenticated public routes**:

- `GET /secrets` — branded send page (Host → brand)
- `POST /secrets/api/create` + `/secrets/api/<upload_token>/…` — upload JSON API
  (presign, multipart, remove, finalize)
- `GET /s/<token>` — download page; `POST /s/<token>/unlock` — password;
  `GET /s/<token>/dl/<file>` — 302 to a presigned GET;
  `POST /s/<token>/report` — abuse report

No file content passes through Odoo: the bytes go straight from the browser to
the S3 bucket (presigned URLs) and come back through a 302 redirect.

## Threat model and mitigations

### Capabilities and tokens

- **Two distinct tokens** per transfer (defence in depth): `upload_token` (draft
  phase only, **revoked at finalisation**) and `token` (the `/s/` link,
  **inert before activation**). UUID v4, compared in **constant time**
  (`hmac.compare_digest`).
- Tokens are stored in clear but **restricted to managers** (`groups=manager`);
  revealing a link goes through a dedicated wizard and is **written to the log**.
- **ACLs**: the public has **no** model access — everything goes through
  `sudo()` behind the token check in the controllers.

- **A token that opens nothing gets one of two answers, not one.** Neither
  discloses metadata, but they are distinguishable from each other:
  - **unknown, malformed, draft or harvested** token → **404**
    (`request.not_found()`);
  - **expired, purged, suspended or out of download budget** → a **neutral page
    with a 200** ("this transfer is no longer available").

  ⚠️ This document long claimed "uniform 404s" for both cases. That was wrong,
  and it is corrected here rather than in the code. The gap is an **existence
  oracle**: whoever holds a token can learn that it was once valid, even after
  it expired. The reach is small — a UUID v4 cannot be guessed, so only someone
  who *already* holds the link can ask the question, and they learn nothing they
  did not already know. Making it uniform remains possible (serve the neutral
  page instead of the 404), at the cost of a `/s/` route answering 200 to any
  string.

### Direct S3 upload

- **Minimally scoped presigned URLs**: a simple PUT with a signed
  `Content-Length` (900 s TTL), and multipart parts signed **in batches of ≤ 20
  on demand** (anti presign-farming, 3600 s TTL). The S3 key is **opaque and
  server-generated** (`<tenant-prefix>/<uuid>/<uuid>` — no filename and no PII
  in the key; the per-tenant prefix isolates instances on a shared bucket).
- **Restrictive CORS**: explicit origins (never `*`), PUT method only,
  `ExposeHeaders: ETag`.
- **Server-side multipart completion**: the part list is rebuilt through
  `ListParts` — client-supplied ETags are never trusted.

### File integrity

- At finalisation: a `HEAD` of each object (existence plus **exact size**) and
  **ETag pinning**.
- Before **every** download: a re-`HEAD` plus an ETag comparison. A malicious
  re-PUT after finalisation (with a still-valid presign) is therefore blocked:
  the download is refused and an `integrity_mismatch` event is logged.
- Download through a 302 to a presigned GET (300 s TTL) with
  `ResponseContentDisposition: attachment` plus `ResponseContentType:
  application/octet-stream` **forced** — never inline rendering, which
  neutralises stored XSS through an HTML/SVG file.
- No client-side SHA-256 at MVP (WebCrypto does not stream — impossible over
  20 GB); the `checksum` field is reserved. An antivirus hook is planned (the
  `scanned` field — the download route already allows only `none`/`clean`).

### Anti-abuse (public page)

- **Sender email required** (no captcha at MVP).
- A `website_url` **honeypot**: filled in → a silent false success, nothing is
  created.
- **In-memory burst rate limits** per IP (creation 10/h, upload API 120/min,
  token failures 20/5 min, password 8/15 min per IP+transfer, abuse 5/day).
  *Known limitation: the state is per worker — a multi-worker deployment
  multiplies the effective ceiling; strong hardening means rate limiting
  upstream (NPM/WAF).*
- **Daily database quotas** (reliable across workers, under a `FOR UPDATE`
  lock): 25 transfers and 10 GB declared per IP per day, 5 transfers per sender
  email per day.
- **An extension deny-list** (executables, scripts and so on), a mandatory
  extension, a sanitised filename (paths, control characters, RTL override,
  ≤ 255); the mimetype is determined **server-side**.
- **Kill switches**: `public_upload_enabled` (the send page) and the
  `suspended` state per transfer (abuse reported).
- **Conditional `Reply-To`.** The header points at the human sender — but
  **only** where the destination is not theirs to choose: a drop page
  (`fixed_recipient`, so the mail can only ever reach the page owner), a brand
  or instance whose **sender allow-list is set**, or a send originated in the
  back office. On a brand that accepts any sender it is **not** set: the reply
  must come back to the brand mailbox, which is the only place such abuse shows
  up. The `From` always stays the brand — SPF/DKIM/DMARC untouched.

### Fields that end up in a mail header

`sender_name`, `subject` and `recipient_emails` are normalised in the model's
`create()`/`write()` (`_clean_line`: CR/LF and control characters removed,
including `U+2028`/`U+2029`, length capped). **The cleaning lives at the ORM
level, not in the controllers**: the finalise route wrote `sender_name` through
its own sanitiser (trimming only), and any ORM/XML-RPC write bypassed the
controllers entirely.

⚠️ What a CR/LF produces there is **not** header injection: Python's `email`
stack **refuses** the value (`ValueError: Header values may not contain linefeed
or carriage return characters`). The real effect is a **silent delivery
failure** — `mail.mail` lands in `exception` while the sender has already read
"transfer ready". That failure is what the guard prevents.

### Abuse desk — the notice stays with the tenant

The abuse notice is the most talkative message this module produces: reference,
brand, **sender**, **full recipient list**, the reason given and the **reporter's
IP**. Its destination is therefore resolved from tenant data only
(`secure.transfer._abuse_desk_email`), in this order:

1. the `abuse_email` setting (Settings → Secure Transfer);
2. failing that, the **e-mail of the company** that owns the brand;
3. as a last resort, the address the brand already sends from — so an empty
   `email_to` does not fail silently in the queue.

No operator address is hardcoded anywhere in the module.

### Password

- Hashed with **pbkdf2_sha512** (passlib, an Odoo core dependency); the hash is
  restricted to the system group. The password **never appears** in the emails —
  a separate channel is assumed.
- A server-side gate (session); failures are logged (`password_fail`) and
  capped.

### Recipient code — bound to the SESSION, not to the transfer

The challenge is kept in the **visitor's session** (`st_otp_chal_<id>`: the hash
of the code plus its expiry), never on the record. Consequences:

- **Intercepting the code is not enough.** Someone reading the recipient's mail
  without the browser that made the request opens nothing: they would need that
  session too. The code alone is inert.
- **A new request replaces the previous challenge**: the earlier code stops
  working as soon as another is asked for. That is deliberate (one live code at
  a time), but it is a trap in automated testing — requesting in one process and
  verifying in another cannot work.
- ⚠️ **A usability corollary worth knowing**: if the recipient opens the link on
  their phone and reads the code on their desktop, entering it on the desktop
  fails — that session never asked for anything. It heals itself (they request a
  new code from the browser they are in), and the page now says so, but without
  that sentence the refusal looks inexplicable.
- Success is a session flag too (`st_otp_ok_<id>`): it follows neither the
  device nor the link, only the browser that passed the gate.

### Access log (Law 25)

- **Append-only, hash-chained** (the `bf_sign_log` pattern): each entry
  references the previous one's hash; `write()`/`unlink()` are blocked at ORM
  level, with a read-only ACL even for managers. Any tampering breaks the chain
  (`verify_chain()`).
- The log records the **filename as of the event**: the evidence survives the
  purge of the objects.
- An assumed fidelity limit: the `download` event attests a **download
  initiated** (a 302 emitted), not the complete receipt of the bytes.
- Life cycle: the purge deletes the S3 objects but **keeps the metadata and the
  log** (never an `unlink`, a house rule); only the weekly GC deletes records
  after `log_retention_days` (365 days).

### Public pages and headers

- **Standalone** QWeb pages (no portal/website layout); security headers on
  every response: **CSP** (with the S3 host added to the send page's
  `connect-src` — the only addition), `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy`.
- The brand host exposes only the product: `/web/login`, `/web/database`,
  `/odoo` and `/xmlrpc` are 302-redirected to the main instance (NPM config).
- User content (message, filenames) is rendered **escaped only** (`t-esc`);
  JSON error responses are generic, with no internal detail.

### Secrets and blast radius

- S3 keys **outside the database** (env / `odoo.conf`) — a database dump or a
  staging refresh never carries production keys away.
- **Isolation through the key prefix (`s3_key_prefix`)**: on a shared bucket,
  each instance writes, purges and sweeps only under its own prefix. Object lock
  is OFF (otherwise purging is impossible). The staging refresh purges the
  inherited records and repoints the prefix so staging cannot act on production
  objects. For stronger isolation, a bucket plus a scoped key per tenant remain
  possible.

## Non-guarantees

- The log's hash chain is **secretless**: it detects tampering but does not
  resist an actor with database write access (who could recompute the chain).
  There is no external anchoring (RFC 3161) at MVP.
- If your S3 provider is **US-owned** (for example IDrive E2), the data may
  reside in Canada (the region is verified by a probe) but the CLOUD Act
  applies — "hosted in Canada" is defensible, "beyond the reach of any foreign
  access" is not (a caveat to document; application-level encryption is
  Phase 3).
- The `/s/<token>` link is a **capability**: anyone holding it (a forwarded
  email, someone looking over a shoulder, browser history) reaches the files,
  subject to the password. The optional password is the mitigation on offer.
- Encryption in transit (TLS) and at rest on the provider's side; **no
  zero-knowledge encryption** at MVP.
