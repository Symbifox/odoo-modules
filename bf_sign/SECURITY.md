# Security — `bf_sign`

## Supported versions

The current version (`18.0.3.13.2`) receives security fixes. Earlier versions do
not: upgrade before reporting.

## Reporting a vulnerability

Please report any vulnerability responsibly to
**security@bluefoxconsultant.com**, without prior public disclosure.

## Scope

The module exposes **unauthenticated public routes** protected by a **per-signer
access token**:

- `GET /sign/<id>/<token>` — signing page
- `GET /sign/<id>/<token>/document` — preview of the original PDF
- `POST /sign/<id>/<token>/submit` — signature submission
- `POST /sign/<id>/<token>/refuse` — refusal to sign
- `GET /sign/<id>/<token>/done` — confirmation
- `GET /sign/<id>/<token>/download` — download of the signed document

## Controls in place

- **Access tokens**: UUID v4 per signer, compared in **constant time**
  (`hmac.compare_digest`) — no enumeration through timing.
- **Anti-brute-force rate limiting** per IP on token failures (sliding window).
  *Known limitation: the state is held in memory and therefore **per worker**; a
  multi-worker deployment multiplies the effective ceiling. For strong
  hardening, put rate limiting upstream (proxy/WAF).*
- **Input validation**: signature images restricted to PNG, size capped,
  integrity verified (Pillow) **before** acceptance; the document restricted to
  an unencrypted, readable PDF with a capped size. The caps are configurable.
- **A chained append-only log**: each entry is linked to the previous one by a
  SHA-256 hash; `write()`/`unlink()` are blocked at ORM level (on top of the
  read-only ACL). Any insertion, deletion or modification of an entry breaks the
  chain and is detectable.
- **Tamper-evident records**: a signed request cannot be deleted.
- **Idempotent finalisation**: a row lock preventing double finalisation on
  concurrent signatures.
- **Integrity verification** available on demand (the log chain plus the sealed
  document's hash plus the RFC 3161 token).

## Trust anchor & non-guarantees

- The log's hash chain is a **secretless** SHA-256 hash: it detects tampering
  but, on its own, **does not protect** against an actor with write access to
  the database (who could recompute the chain). **Off-platform anchoring** rests
  on **RFC 3161 timestamping** (an independent timestamping authority) —
  recommended for documents that matter.
- Tier-1 verification of the RFC 3161 token checks the **hash match**
  (messageImprint) and the **"granted" status**; it **does not**
  cryptographically verify the token's CMS signature against the TSA's CA chain
  (not implemented to date).
- The module produces a **simple electronic signature (SES)**, **not** an
  advanced (AES) or qualified (QES) signature.
- RFC 3161 timestamping is **optional** and off by default.

## The seal's encryption key (Fernet)

The sealing certificate (and its private key) is stored **Fernet-encrypted** in
`ir.config_parameter`. The Fernet key is read, in order of precedence:
`BF_SIGN_FERNET_KEY` (env) → `bf_sign_fernet_key` (`odoo.conf`) → the shared
`bf_security_awareness` key (env/conf) → the `bf_sign.fernet_key` system
parameter (database).

- **Recommended (hardened)**: keep the key in the environment or in
  `odoo.conf`. A copy of the database is then **not** enough to decrypt the
  certificate.
- **Self-service option (Settings → Electronic signature)**: an administrator
  (`base.group_system`) can generate or paste the key from the UI; it is then
  stored in the database. **The trade-off**: the key then lives **in the
  database**, next to the secrets it protects — a copy of the database exposes
  both. The key is never returned to the browser (the input field is
  write-only, and the status only shows its provenance). env/conf keep
  precedence: you can move the key into `odoo.conf` at any time to harden it.
- Changing an already-active key while a certificate exists is **refused** (it
  would make the certificate unreadable); delete the certificate first.

## Deployment good practice

- Serve the instance over **HTTPS** only, and configure `web.base.url` correctly
  (the signing links depend on it).
- Enable a **trusted TSA** for documents that matter (see `README.md`).
- Restrict the `Signature / User` and `Signature / Manager` groups to authorised
  people.
