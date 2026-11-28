# Secure Transfer (`bf_securetransfer`)

An in-house "WeTransfer" for secure file transfer, native to Odoo 18 Community:
**direct browser → S3** upload (IDrive E2, `ca-east-1` region) through presigned
URLs (simple PUT + multipart), **tokenised links** with expiry, optional
password and OTP, a **tamper-evident access log** (hash chain, a Law 25
artefact), **automatic purging**, **multi-brand** resolution by hostname,
**personal drop pages** (`/to/<slug>`), **anti-piggyback allowlists** and
**automatic suspension on abuse reports**.

- **Version**: `18.0.1.26.0`.
- **Licence**: **BUSL-1.1** — production use allowed for your own internal business operations; providing the module as a product or service to third parties (hosted, managed or resold) requires a written agreement. Converts to **LGPL-3.0-or-later** on **2030-11-28**. See [`LICENSE`](LICENSE).
- **Threat model & non-guarantees**: see [`SECURITY.md`](SECURITY.md).
- **Multiple tiers**: the multi-brand system supports a limited free tier
  (default brand, optional "Powered by" mention) and a paid white-label tier on
  a dedicated domain (higher limits). The differentiator is **Law 25 compliance
  and data residency in Canada**.

---

## Features

**Transfer**

- **Direct browser → S3** upload through presigned URLs: Odoo signs, and never
  proxies the bytes. Simple PUT (≤ 64 MB) and **multipart/resumable** for
  multi-gigabyte files (16 MB parts, batched presigns, 3 concurrent parts,
  in-session resume through `ListParts`).
- **Integrity**: at finalisation each object is verified server-side (existence
  + exact size) and its **ETag is pinned**, then **re-verified before every
  download** — blocking the "re-PUT malware with a still-valid presign" attack.
- **Download** = a 302 redirect to a presigned GET (300 s TTL), with
  `attachment` + `octet-stream` forced (never inline rendering, which kills
  stored XSS).
- **Two sending modes, two tabs**: **Files** (default) or **Message only** (a
  secure note with no file — for instance to pass on a password). In **Message
  only** mode the content **never appears in clear text in the email**: the
  notification carries only the link, and the message can be read only on the
  secure page (time-limited, logged).
- **Subject** (optional, 120 characters): the one line that tells the recipient
  what this is. It **leads** the mail subject ("2026 leases — Jane Doe shared
  files with you (TRF-2026-00123)"), heads the mail body and becomes the title
  of the download page. It travels **in the clear** in the header by design: the
  gates (password, recipient code) hold the **content**, never this line — which
  is what lets it inform someone before they open anything. It deliberately
  stays **out of the tab title**, which the gate pages share with the content
  page, so a forwarded link does not spill it before the gate. Normalised in the
  model's `create()`/`write()` along with the other header-bound fields (CR/LF
  and control characters removed, length capped) — a controller alone would be
  bypassed by any ORM write.
- **Conditional `Reply-To`**: "Reply" reaches the person rather than the brand
  mailbox — but only where the destination is not the sender's to choose (a drop
  page, a brand with a sender allow-list, or a back-office send). On a brand open
  to any sender the header is not set: the reply must come back to the brand
  mailbox, the only place abuse surfaces. The `From` stays the brand, so
  SPF/DKIM/DMARC are untouched. See [`SECURITY.md`](SECURITY.md).
- **Code-protected secure message (recipient OTP)**: from the backend, a send
  can **hold the content behind a one-time code** delivered by email or SMS —
  the message is shown only after the recipient proves their identity. Sender-
  side send confirmation by code is available too.
- **Field order designed against friction**: email and recipients sit **above**
  the drop zone; the sender's email is **optional at drop time, required at send
  time** (you can drop a file before typing your email without an error).

**Brands & pages**

- **Multi-brand** (`secure.transfer.brand`) resolution by hostname: domain,
  visuals, limits, "Powered by" mention, allowlists, billing. Aligned **by
  default on the in-house branding** (`appointment_brand_*` then
  `report_brand_*`, through `getattr` — no hard dependency on bf_branding /
  bf_onboarding_base).
- **Personal drop pages** `/to/<slug>`: a "Dropbox" page where visitors can only
  send to **one fixed recipient** (the page's owner). The recipient is **forced
  server-side** at create AND at finalize — a drop cannot be redirected
  elsewhere.
- **Automatic light/dark theme** (`prefers-color-scheme`); the brand keeps its
  colours.
- **Dashboard** (graph + pivot views): volume and downloads by brand and by
  state.

**Security & anti-abuse**

- **Anti-piggyback allowlists**, on both the sender and the recipient side, per
  brand **or** as a tenant-wide default (Settings): full address, `@domain` or
  bare domain. Prevents a third party using an instance that is not theirs, or
  relaying files to arbitrary addresses.
- **Two optional OTP flows** (tenant settings, OFF by default): a **sender OTP**
  (confirms a code before sending — anti-impersonation) and a **recipient OTP**
  (a code before download — a Law 25 gate). 6-digit codes, hashed, 15-minute
  TTL. The recipient code can be required from **three** places: the
  instance-wide setting (every send), a **checkbox on the public form** (the
  sender decides for their own send), and a backend button that **arms the gate
  after the fact** on a transfer already out — without recalling the e-mails
  that have already been delivered.
- **The share link does not survive in the record's log.** The link e-mails are
  stored on the transfer (`mail.mail` inherits `mail.message`), so the raw token
  used to sit in clear in the chatter — around both the manager-only `token`
  field and the journaled reveal wizard. The token is now masked as soon as the
  queue has delivered the mail, **unless** a recipient code guards the content
  (the link alone then opens nothing). The predicate is re-evaluated on every
  sweep, so turning the instance-wide setting off masks the older messages too.
  A mail still queued is never touched — it would go out with a dead link.
- **Optional password** (passlib pbkdf2_sha512), **never** sent in the link
  email.
- **Burn after download** (`burn_after_download`) and **download notification**
  (an email to the sender on the first download) — per brand.
- **Abuse report → automatic suspension**: the link goes dark immediately
  (`state = suspended`) and stays that way until an administrator intervenes; a
  detailed email goes to `abuse_email` (failing that, the company's email), a
  neutral notice to the recipients, and an activity to the managers.
  Reactivation is `action_reactivate`.
- **Baseline anti-abuse**: sender email required at send time, honeypot, per-IP
  rate limiting (in memory), **daily DB quotas** (transfers/IP, bytes/IP,
  transfers/sender — advisory locks against TOCTOU), an extension deny-list,
  filename sanitisation, and uniform 404s on token fuzzing.
- **Hardened headers** plus a strict **CSP** (connect-src limited to the S3
  endpoint, on the send page only), `X-Frame-Options: DENY`, nosniff,
  Referrer-Policy.

**Emails & Law 25**

- **Branded emails** (the link to recipients, a receipt to the sender), with
  visuals shared with the pages through `brand._visuals()`.
- **Language follows the contact**: if the address matches a `res.partner`
  record, the email goes out in `partner.lang`; otherwise in the visitor's
  locale. Sent individually per recipient.
- **A tamper-evident access log** (`secure.transfer.access.log`, a hash chain
  cloned from `bf_sign_log`): every event (created, finalised, sent, viewed,
  password OK/failed, OTP OK/failed, download, past expiry, integrity, report,
  suspension, purge, and so on) goes through a single control point. Verifiable
  with `verify_chain()`. A Law 25 artefact retained even after the objects are
  purged.
- **Access certificate (PDF)** — the log made defensible: the integrity verdict
  is **recomputed at print time**, file sizes are the ones **confirmed by the
  server** (not the ones the uploader declared) along with their ETag, all
  timestamps are explicit UTC, and the document ships **the recipe to recompute
  the chain yourself** (SHA-256 of the previous hash plus the canonical
  payload). Without that recipe a certificate *asks* for trust instead of
  producing it. ⚠️ **Who printed a downloaded file remains untraceable** — that
  only exists in an online viewer with no download. Do not promise it.
- **Download watermark** (`watermark_downloads`, per brand, OFF by default):
  each PDF is stamped with the recipient's name and the download timestamp.
  Odoo then serves the bytes itself instead of redirecting to storage;
  non-PDF files keep the direct redirect.
- **Data-room presets** (`secure.transfer.template`): a link's configuration,
  named and replayed — delivery mode, lifetime, visitor cap, per-person
  download budget, admitted domains, agreement. The sender picks a preset in
  the wizard and the rest fills itself. Three ship with the module: *standard*
  (30 days / 25 visitors), *wide audience* (100 visitors, no join notice) and
  *confidential* (7 days / 10 visitors / 3 downloads). None carries a **brand**,
  so a preset lands on whichever brand the sender already chose. A combination
  the send would reject is refused **at save time**, and a preset the effective
  brand cannot fully honour applies partially and **says so** — falling back
  silently to "named recipients" is exactly the failure this model exists to
  prevent. Extension point `_apply_vals()` for companion modules.

---

**Who has not come, and reminding that one person** (since 1.23.0)

- **Named recipients are registered at send time**, in the *Not yet visited*
  state. Before, the audience line was born from the **visit**: a recipient who
  never clicked appeared nowhere, and "who has done nothing" could only be read
  by comparing the address list with the Visitors tab by hand. The Visitors tab
  is now the complete list — who downloaded, who is stuck on their code, who
  never came — with a banner counting those absent.
- **Remind a single person**: a *Resend* button on their line. The email is the
  **same** as the first one (a reminder should look like what it reminds of), it
  goes to that address and no other, and it is logged by name
  (`audience_resent`). The header button now reads "Resend the link to
  **everyone**", so the two gestures can no longer be confused.
- **Automatic reminder of absentees**, per brand and **off by default**
  (`st_absentee_reminder_enabled` / `_delay_hours` / `_max`, hourly cron). Only
  a person who has **not even asked for their code** is reminded: whoever
  downloaded needs nothing, and whoever is stuck on a code already has the email.

**One send per recipient** (since 1.23.0, backend wizard)

- A wizard checkbox: *One send per recipient*. The upload stays **single**; the
  objects are copied **inside the bucket** (`s3.copy_object`, `CopyObject` under
  5 GiB, **multipart** copy above), so no second pass from the workstation and
  **no byte** through Odoo.
- Each person gets **their own** link, with their own expiry to extend, their
  own download budget, their own log, and a watermark that can finally name
  someone instead of an IP.
- The **first** recipient keeps the original transfer: a batch of N people
  stores **N** copies, not N+1. An "In this batch" button links the sends.
- ⚠ `recipient_emails` keeps the **batch** list on every send (it is what the
  sender's receipt announces and what an operator searches by). **Who may OPEN
  a link is read from `_recipient_list()`**, which returns only
  `fanout_recipient`: mixing the two would make the N links interchangeable.
- The split **copies every setting of the send by default**, including those a
  bridge module adds (confidentiality agreement, expiry policy): exclusion is
  explicit (`_FANOUT_NOT_CARRIED`), because a forgotten setting would open N
  links without the gate the original send carried.

**Prepare a send, send it later** (since 1.23.0, backend wizard)

- **"Keep as draft"** button in the wizard: the files go to storage, **nothing**
  goes out. No link exists yet (the token only rotates at activation), no email
  leaves, and **nobody** is registered on the Visitors tab. ⚠ Every send check
  is run **when keeping** (brand allowlist, offered retention, daily quota):
  discovering a refusal three days later means an upload for nothing.
- On a draft's form: **Add a file**, remove a file with its line's button, then
  **Send**. Adding goes through the same `_backend_add_file` as the wizard, so
  through the deny-list, the brand's caps, the sanitised name and the opaque
  key. Removal exists **only** on a draft: after the send, bytes have been
  served and the log is evidence.
- 🔴 **Who may act on a draft: its author, or a manager.** A holder of the
  **User** group only acts on a draft they prepared (*Prepared by* field); a
  **Manager** acts on all. The group alone was not enough: it is read-only on
  `secure.transfer` and the four gestures elevate with `sudo()`, so a colleague
  in the same role could delete the bytes of someone else's draft or send their
  incomplete file. The gate is single (`_assert_draft_editable`) and repeated
  where bytes come in (`action_add` of the add-file wizard), because a public
  method is an RPC surface. ⚠ A draft born on the **public page** has no
  operator: only a manager can pick it up, on purpose.
- ⚠ The draft path goes back through the wizard's `action_send()`, with a
  context flag (`st_keep_draft`): refusals a **bridge module** raises by
  overriding that method (the confidentiality agreement of
  `bf_securetransfer_sign`) therefore protect the draft by construction.
- **The hourly clean-up no longer touches a kept draft**, with no end date
  (`draft_kept`, excluded in `_gc_drafts_domain`). A draft that is **not** kept
  is still harvested after `draft_ttl_hours` (24 by default), stored objects
  included: it is an abandoned upload, not a file being prepared.
- 🔴 **The only end that "no end" has**: the bucket lifecycle rule
  (`st-expire-orphans-<prefix>`) deletes every object under the prefix after its
  delay, whatever Odoo thinks. A kept draft's form **shows that date**, and
  raises a red banner two weeks before. The date is the one actually **posted**
  on the bucket (recorded by the action that posts it), never a computed one;
  as long as no rule has been posted from this database under this key prefix,
  the form says **"storage net unknown"** rather than invent a date. Since
  1.26.0 the rule follows the longest retention by itself (see Operations).
- Log: `draft_kept`, `draft_file_added`, `draft_file_removed`. With
  `finalized`, these dates frame **what was prepared, and when** — the Law 25
  trace of a file assembled before it was sent.
- The menu list opens on **active and draft** transfers (the two filters
  combine): a kept draft that does not show is a lost draft.

## Flow overview

A visitor opens the public send page (`/secrets`, or `/to/<slug>` for a personal
drop), chooses **Files** or **Message only**, fills in their email and (outside
a personal drop) the recipients, drops their files, and gets a
`https://<brand domain>/s/<token>` link. The bytes go **straight from the
browser to the S3 bucket**. At finalisation each file is verified (existence,
size, **ETag pinning**), the branded emails go out (the link to recipients in
their language, a receipt to the sender — **never the password**), and the
transfer lives until its expiry, when the S3 purge deletes the objects **while
keeping the metadata and the log**.

Life cycle: `draft` → (per-file upload) → `active` → `expired` (date / download
budget / burn) → S3 purge (`deleted`, metadata + log kept) → hard GC after
`log_retention_days`. Additional states: `cancelled` (harvested drafts) and
`suspended` (the abuse kill switch).

---

## Architecture

### Models

| Model | Role |
|---|---|
| `secure.transfer` (`mail.thread`) | The transfer: tokens (an ephemeral `upload_token` plus a sharing `token`), state, sender/recipients, message, password, OTP, expiry, counters, `burn_after_download`/`notify_on_download`. Split sends: `fanout_recipient` (the only person this link opens for) and `fanout_parent_id` (the batch). Kept drafts: `draft_kept`, `draft_operator_id`, and the storage-net date the form shows. |
| `secure.transfer.file` | A file: sanitised name, size (`Float` — never `Integer`, which overflows past 2.1 GB), extension (deny-list), an **opaque** `s3_key` (uuid, no PII), pinned ETag, state, multipart fields (`s3_upload_id`, `part_size`, `parts_total`). |
| `secure.transfer.brand` | A brand: `domain`, `slug` + `fixed_recipient` (drop page), visuals, limits, `tier`, `sender_allowlist`/`recipient_allowlist`, `powered_by` + `powered_by_name`, absentee reminder settings, billing. |
| `secure.transfer.audience` | **A person a transfer has reached**: identity (email **or** mobile), state (`invited`/`pending`/`confirmed`/`blocked`), reminders (`resend_count`), abuse counters held **in the database** (codes sent, SMS, cooldown) and a per-person download budget. Created in both modes: in open audience it ADMITS, with named recipients it ANCHORS (per-name watermark, budget, agreement). Deletion is blocked outside the GC — it records who was let in. |
| `secure.transfer.access.log` | The append-only hash-chained log (Law 25). Writes blocked outside the control point; deletion only by the weekly GC. |
| `secure.transfer.template` | **A data-room preset**: the settings of a link, named and replayable (mode, lifetime, visitor cap, per-person download budget, admitted domains, agreement). Carries no brand on purpose. Two constraints refuse at save time what would have failed in production: open mode on a brand that does not offer it, and SMS together with a domain list. |
| `res.config.settings` | Tenant settings (`ir.config_parameter` params). |
| `reveal.link.wizard` | The "Reveal link" wizard (tokens stored in clear, `groups=manager`). |

### Tree

```
bf_securetransfer/
├── __manifest__.py            # depends [web, mail, portal]; external_dependencies boto3
├── hooks.py                   # post_init_hook: en_CA email translations (jsonb)
├── README.md / SECURITY.md
├── controllers/main.py        # public pages (/secrets, /to/<slug>, /s/<token>…)
├── controllers/upload_api.py  # upload JSON API (create/presign/multipart/finalize/confirm)
├── models/s3.py               # the ONLY boto3 file (lazy): client, presign, head, multipart, CORS
├── models/secure_transfer.py  / _file.py / _brand.py / _access_log.py / res_config_settings.py
├── wizards/reveal_link_wizard.py
├── security/  data/  views/   # ACLs + groups; sequence, default brand, crons, 3 templates; backend views + public QWeb
├── static/src/js/st_upload.js # plain JS: drag-drop, XHR PUT progress, chunking, resume, tabs, drop mode
├── static/src/{js/st_download.js, css/st_public.css}
├── migrations/18.0.1.1.0/post-migrate.py
├── i18n/ (pot + fr_CA + en_CA)
└── tests/ (lifecycle, access_log, host_resolution)   # 63 tests, S3 fully mocked
```

### Routes

| Route | Type | Role |
|---|---|---|
| `GET /secrets` | http | Branded send page (Host → brand). |
| `GET /to/<slug>` | http | Personal drop page (fixed recipient). |
| `POST /secrets/api/create` | json | Draft → `{upload_token, limits}`. Email optional; `drop_slug` for a personal drop. |
| `POST /secrets/api/<ut>/presign` | json | Registers a file → simple PUT or a multipart plan. |
| `POST …/multipart/{initiate,sign,complete,abort,status}` | json | The multipart cycle (complete rebuilds from `ListParts`, never client ETags). |
| `POST …/remove` | json | Removes a file before finalize. |
| `POST …/finalize` | json | Verifies, activates, sends the emails → `{share_url}`. |
| `POST …/confirm` and `…/confirm/resend` | json | Sender OTP confirmation. |
| `GET /s/<token>` | http | Download page / password gate / OTP gate / neutral page. |
| `POST /s/<token>/unlock` | http | Password submission (session flag). |
| `POST /s/<token>/otp-request` and `/otp-verify` | http | Recipient OTP gate. |
| `GET /s/<token>/dl/<file_id>` | http | ETag re-check → 302 to a presigned GET. |
| `POST /s/<token>/report` | http | Abuse report → automatic suspension + emails + activity. |

---

## Languages (18.0.1.22.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English. Each text is written in the language of whoever will read it, not of whoever triggers it (an anonymous visitor, a scheduled job, an operator). The public pages follow the family of the visitor's browser language, English or French, even on a database where English is active only as en_US; their title, durations, file sizes and report confirmation follow too, and so do the upload API's JSON errors, whatever language the session already held. The access log notes are written in the transfer's language, and the access certificate is rendered in it, whoever prints it. The abuse desk reads its alert in its contact's language, else the company's; each recipient reads the suspension notice in their own; each manager receives the abuse activity, and Odoo's "assigned to you" notice, in their language. The new visitor notice follows the sender; a code requested from the page by someone without a contact record follows the page. The CSV export of the log follows the operator, the failing purge activity the administrator, and the S3 bucket check verdicts the user who runs it. File sizes carry the reader's unit (KB or Ko) on the pages and in the emails. An automatically created personal drop page is named in the company's language, and its French name stays exactly `Envoi de fichiers | <name>`, so existing pages are not renamed. On upgrade, the shipped email templates and scheduled jobs switch to English only where the database still holds the text that was shipped (including forms shipped by earlier versions); an edited template is left alone.

## Dependencies

- **Odoo modules**: `web`, `mail`, `portal` (not `website`: the public pages are
  standalone, and the website router must never hijack a brand Host).
- **Python**: `boto3` + `botocore` (pinned in the tenants' Dockerfiles — a new
  final pip layer, do not touch the pyHanko layer). The import is lazy: without
  boto3 the module installs, but any S3 operation raises an explicit error.

---

## Getting started (operator)

### 1. S3 access keys — `odoo.conf` or the environment, never the database

The keys travel **outside the database** (a dump or a staging refresh would
carry production keys away). In order of precedence:

```ini
# environment variables (take precedence)
BF_SECURETRANSFER_S3_ACCESS_KEY=…
BF_SECURETRANSFER_S3_SECRET_KEY=…

# or an odoo.conf block
bf_securetransfer_s3_access_key = …
bf_securetransfer_s3_secret_key = …
```

The bucket can be **shared between several instances** or dedicated. Each
instance is isolated by its **key prefix** `s3_key_prefix` (for example
`transfers-prod`, `transfers-staging`): it is that prefix — not the bucket name
— that guarantees one instance's purge or orphan sweep never touches another's
objects. **Make `s3_key_prefix` unique per instance.** **Object lock must be
off** — otherwise purging is impossible. After cloning a production database
into a test environment, empty the `secure_transfer_*` tables (inherited from
the dump) and repoint `s3_key_prefix` + `public_base_url` at the test values, so
the test never acts on production objects.

### 2. Settings (Settings → Secure Transfer)

Non-secret, stored in `ir.config_parameter` (`bf_securetransfer.` prefix):

| Setting | Default | Role |
|---|---|---|
| `s3_endpoint_url`, `s3_region`, `s3_bucket` | — / `ca-east-1` / — | IDrive E2 endpoint |
| `s3_key_prefix` | — | Per-instance isolation prefix (unique!) |
| `s3_path_style` | `1` | Path-style addressing |
| `cors_origins` | — | Origins allowed to upload (never `*`) |
| `public_base_url` | — | Public URL when the brand has no domain (set it explicitly rather than relying on `web.base.url`) |
| `public_upload_enabled` | `1` | Kill switch for the send page (download links keep being served) |
| `presign_put_ttl` / `presign_part_ttl` / `presign_get_ttl` | 900 / 3600 / 300 s | Presigned URL lifetimes |
| `multipart_threshold_mb` / `part_size_mb` / `mpu_sign_batch_max` | 64 / 16 / 20 | Multipart switchover, part size, presign batch |
| `default_free_max_transfer_mb` / `default_paid_max_transfer_mb` | 2048 / 20480 | Per-tier caps |
| `default_max_files` | 25 | Files per transfer |
| `default_free_max_retention_days` / `default_paid_max_retention_days` | 7 / 90 | Per-tier retention |
| `draft_ttl_hours` | 24 | Harvesting of abandoned drafts |
| `log_retention_days` | 365 | Log retention after purge |
| `rate_create_per_hour`, `quota_daily_transfers_per_ip`, `quota_daily_bytes_per_ip_mb`, `quota_daily_transfers_per_sender` | 10 / 25 / 10240 / 5 | Anti-abuse |
| `default_sender_allowlist` / `default_recipient_allowlist` | — | Tenant default allowlists (a brand can override) |
| `require_sender_otp` / `require_recipient_otp` | `0` / `0` | Enables the sender / recipient OTP gates |
| `abuse_email` | — (company email) | Recipient of abuse notices |

### 3. The "Configure the S3 bucket" action

A button in Settings → Secure Transfer, **idempotent**, to re-run after any
change to the CORS origins:

- applies the **CORS** policy: explicit origins from the `cors_origins` setting
  **plus** the union of active brand domains, **PUT only**,
  `ExposeHeaders: ETag` (**mandatory** — without it multipart fails silently in
  the browser), `MaxAge 3600`;
- posts the lifecycle rules on the tenant's key prefix
  (`AbortIncompleteMultipartUpload` 2 d, which IDrive E2 refuses, then
  `Expiration` at `max(60, longest retention + 15)` days) — a safety net behind
  the purge — and **records the delay it posted**
  (`bf_securetransfer.s3_lifecycle_applied`), which a kept draft's form shows.
  The net **never goes down**: re-run after a brand lowered its retention, the
  action keeps the delay already posted (read back from the bucket), otherwise
  sends granted before the change would be deleted while still promised. When
  a retention later goes **up**, the scheduled job "raise the bucket lifecycle
  net" posts it again by itself (see Operations);
- runs the **probes**: `GetBucketLocation == ca-east-1` (proof of residency),
  object lock OFF, a put/head/get/delete round trip, signed `Content-Length`
  enforced, batched `DeleteObjects`, a full multipart cycle. The report is shown
  on screen.

### 4. CORS required (a reminder)

Direct upload requires **every public origin** (the send page) to appear in
`cors_origins` or among the active brand domains:
`https://secret.example.com`, the staging origin for QA, and every future brand
domain. Downloads are 302 navigations, so no CORS.

### 5. Reverse proxy (public host)

Put each public domain (for example `secret.example.com`) behind a reverse proxy
forwarding to the Odoo instance. The configuration points that matter:

- `client_max_body_size 16m` — only JSON goes through Odoo, the bytes go
  straight to the bucket;
- a `/websocket` location → port 8072 (mirroring the main Odoo host);
- a **302 redirect** of `/web/login`, `/web/database`, `/odoo`, `/xmlrpc` to the
  main instance — the brand host exposes only the product;
- **⚠️ mandatory hardening**: the proxy MUST **overwrite** `X-Forwarded-Host`
  (`$host`) and `X-Real-IP`/`X-Forwarded-For` (`$remote_addr`), never relay the
  client value — otherwise a visitor spoofs their IP (bypassing the rate limits)
  and selects a paid brand (high limits plus emails under a client's brand). The
  same weakness is inherited from bf_sign/bf_policy: the control lives at the
  proxy, not in the code;
- `X-Content-Type-Options: nosniff` plus `Referrer-Policy`.

### 6. Brands

Configuration → Brands. The **Default** brand (with no domain) serves every
unknown host; its visuals fall back to the company branding. A paid brand: bare
domain (`secret.client.com`), logo/favicon/colours, `paid` tier, its own limits
(0 = the configuration default), `powered_by` unticked where relevant,
`sender_allowlist`/`recipient_allowlist` to lock down usage.

**"Powered by"**: when the box is ticked, the public page, the four emails and
the access certificate print the **same** name, the brand's "Credited name"
field (`powered_by_name`, "Symbifox" by default; empty = Symbifox). The name is
**never** read from the company: the company served is not the one credited. A
white-label brand unticks the box, and nothing is printed, certificate
included.

### 6b. Personal drop page (`/to/<slug>`)

On the brand record, fill in the **Page identifier (slug)** (for example `drop`)
and the **Single recipient** (the address that will receive everything), plus an
optional display name. The brand becomes a drop page served at `/to/<slug>`: the
recipient field is hidden and **forced server-side**. Ideal for a "Send me a
file" link (email signature, website). Nothing to configure on the proxy if the
page lives under a domain that is already served (for example
`https://example.com/to/drop`).

### 7. Onboarding a paid brand domain

1. **The client's DNS**: `CNAME secrets.client.com → <the instance's domain>`,
   **DNS-only**. Check with `getent hosts secrets.client.com`.
2. **Reverse proxy + TLS certificate** for the new domain, with the same header
   hardening as in step 5.
3. **Brand record**: bare domain, **Paid** tier, visuals, "Powered by"
   unticked, high limits, `sender_allowlist` (for example `@client.com`),
   `partner_id`, then the **"Configure the domain (CORS)"** button.
4. Monitor the new domain's certificate.

### 8. Billing the paid tier

Each paid brand carries `billing_active` / `billing_ref` / `price_year` fields.
Billing itself is up to your own tooling: a simple cost register with rebilling
on request, or **automatic recurring** billing through `sale.subscription`
(Enterprise) or the OCA `contract` module.

---

## Operations

- **Crons**: a daily purge at 03:15 (expired → batched S3 deletion, metadata +
  log kept), an hourly GC of abandoned drafts (multipart abort + object
  deletion), a weekly GC of logs past retention (the only path that deletes log
  entries), an hourly reminder of absent recipients (off by default, per
  brand), and the **bucket net** at 03:40: raising a brand's maximum retention,
  or a tier default in the settings, triggers this job at once; it raises the
  `st-expire-orphans-<prefix>` rule and records the new delay. The daily pass
  also reads the rule back from the bucket and puts it back when the shared
  bucket configuration was rewritten lower (the read-merge-write of a shared
  bucket is not atomic). It posts nothing while the net is unknown, never
  lowers the rule, and never raises (Odoo 18 disables a job that keeps
  failing).
- **Purge failures**: a per-transfer counter; ≥ 5 failures → an admin activity.
  If the S3 endpoint is unreachable, the cron gives up cleanly and catches up on
  the next run.
- **Backups**: the buckets are **excluded by design** (ephemeral content —
  backing them up would contradict the retention promise). Recorded as "no
  backup expected — by design" in the backup coverage audit.
- **Monitoring**: track the TLS certificate of each public domain (automatic
  renewal recommended).
- **Access log**: the Access log menu — a Law 25 artefact; chain integrity is
  checked with `verify_chain()`.

## Tests

`672 tests` across 32 files (lifecycle, S3 gateway, multipart, the public HTTP
routes, email/i18n, wizards, crons, operator actions, ACL surface, brand
provisioning, download gates, chatter link retention, one send per recipient,
kept drafts and their owner gate, "Powered by", wizard owner rules, the bucket
lifecycle net). Run on a fresh database: `tests/common.py` (`BaseNeuve`)
activates the two Canadian languages and an outgoing mail domain. **Every** S3 interaction
is mocked on
`odoo.addons.bf_securetransfer.models.s3`: the suite runs without boto3 and
without a reachable endpoint. To run it on staging:

```
docker exec odoo-staging odoo -d staging -u bf_securetransfer \
    --test-enable --test-tags /bf_securetransfer --stop-after-init
```

## Deployment

Install / update through your usual Odoo deployment procedure:

```bash
odoo -d <database> -u bf_securetransfer --stop-after-init
```

The migration making `sender_email` nullable is applied automatically by Odoo on
`-u`.

---

## Roadmap

**Phase 2 — delivered**: burn-after-download, notify-on-download, paid custom
domains (automatic CORS, runbook), dashboard, billing wiring, sender +
recipient OTP, allowlists, personal drop pages, message-only mode, light/dark
theme, email language following the contact.

**Phase 3 — upcoming**: ClamAV (the `scanned` field already ships, and the
download route honours it), a "download all" ZIP (excluded at MVP: proxying
multiple gigabytes), zero-knowledge application-level encryption (opaque S3 keys
are ready; incompatible with ClamAV — a positioning decision), a cross-session
multipart resume UI, automatic recurring billing.

**Law 25 caveat** (to be documented in the product's privacy impact
assessment): IDrive is a **US** processor (CLOUD Act). "Hosted in Canada" is
defensible; "beyond the reach of any foreign access" is not, until Phase 3
(zero-knowledge encryption).

---

## Version log

| Version | Highlights |
|---|---|
| `18.0.1.26.0` | **The bucket net follows the longest retention.** The lifecycle rule `st-expire-orphans-<prefix>` kept the value of the day "Configure the S3 bucket" was last run: raising a brand's retention did not post it again, and a kept draft's form announced the old date. Raising a brand's `max_retention_days` or a tier default now **triggers** the new scheduled job `cron_st_sync_lifecycle`, which posts the rule again and records the delay; the draft form follows with no manual step. Nothing is sent to the provider inside the screen's transaction (a slow bucket would make saving a brand fail). **Ratchet**: neither the job nor the manual action lowers the net any more, because a brand brought down from 90 to 30 days still has sends granted for 90; the ratchet also reads the rule **back from the bucket**, not only the recorded value, and the daily pass restores our rule when a neighbour rewrote the shared configuration lower. Unknown net, no storage, or a provider refusal: nothing is posted or recorded, and the job does not raise. `s3.apply_lifecycle()` comes out of the action to be shared. **Upgrade (`-u`) required** (new scheduled job). |
| `18.0.1.25.1` | **Wizards are scoped to their owner.** Each wizard carries a global record rule on its creator, so an internal user can no longer read a colleague's wizard rows before the autovacuum clears them (see "Wizards are owner-scoped" below). |
| `18.0.1.25.0` | **"Powered by" names Symbifox, on all three surfaces.** The public page and the four emails read the public name of the company **served**, and the access certificate its **legal** name: a client could end up crediting itself, and one brand printed two different names on the page and on the certificate. New per-brand field `powered_by_name` ("Credited name", default "Symbifox"), the single source of the three surfaces through `_powered_by_label()`. The `pre-migrate` migration creates the column itself so it can **log** every brand that switches, with what it displayed before; a name already chosen is left alone. **Upgrade (`-u`) required** (new column, certificate). |
| `18.0.1.24.0` | **Two public-page fixes.** File sizes follow the reader's language for the number as well as the unit ("2.9 Mo" mixed two conventions in one sentence), and the brand logo is served from a **relative** address, so a page served from another host no longer shows a broken image; the emails, which need an absolute address, already prefix it themselves. |
| `18.0.1.23.0` | **One send per recipient, and seeing who never came.** Named recipients get their audience line **at send time** (`invited`, "Not yet visited"); per-line **Resend**, and a per-brand automatic reminder, off by default. **"One send per recipient"** in the wizard: N links, N expiries, N logs, objects copied **inside the bucket** without a second upload. `_recipient_list()` becomes the **only** source of "who may open this link". **Keep a send as a draft**: keep, add and remove files, send later; the hourly clean-up spares a kept draft, whose form shows the date the bucket lifecycle would delete its bytes. Who may act on a draft: its author, or a manager. **Upgrade (`-u`) required** (new columns, new state, catch-up migration for live sends). |
| `18.0.1.22.0` | **Each text in its reader's language.** Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. The public pages follow the visitor's language, the access log and the certificate the transfer's, and each email, activity and alert its reader's. File sizes carry the reader's unit. The migration switches the shipped email templates and scheduled jobs to English only where they were not edited. See the Languages section. |
| `18.0.1.21.9` | **Public pages on a database without fr_CA.** The pages pushed the product's default locale, fr_CA, into the request context as is, and Odoo refuses a language that is not active: on any database without fr_CA every public page answered 400 « Invalid language code ». The context, the e-mails and the access certificate now fall back to an active language (fr_CA when active, else the company's language, else the first active one). The stored locale and the page's strings do not change. |
| `18.0.1.21.8` | **Extensible draft clean-up.** The hourly draft harvest now takes its domain from `_gc_drafts_domain(cutoff)`, so a module that deliberately keeps a draft open past the TTL (a document request waiting for its files until its own deadline) can leave its drafts out by extending the method. Nothing changes for the base module alone. |
| `18.0.1.21.6` | **Per-code attempt counter.** The six-digit code challenge lives in the session, and the only brake was an in-memory limiter per (IP, transfer): one cookie replayed from rotating IPs got the full allowance again on every address and every worker. Failed attempts on the code in flight are now counted in the database (`otp_verify_fails` on the audience line), read and written under a row lock, five per code sent; past that the code is burnt and a new one must be requested. Sends stay capped per identity (five codes an hour for a named recipient), so attempts stay bounded per hour. The brand is also chosen from the host Odoo keeps behind a proxy (`proxy_mode`), not from the first raw `X-Forwarded-Host` element a client can set. Regression tests for both. |
| `18.0.1.21.5` | **Security fixes.** A **blocked** named recipient can no longer get back in by asking for a new code: sending and confirming a code now go through the same admissibility check that reads the block. Codes sent to one named recipient are **capped per hour**, so a shared link cannot be used to flood someone with e-mail or paid SMS from rotating addresses. The public **abuse report** no longer takes a transfer offline on the token alone: it raises a flag for the owner to review. Regression tests for each. |
| `18.0.1.21.4` | **Transfer e-mails now leave within seconds.** The link, the receipt, the manual resend and the download notice are queued (`force_send=False`) and used to wait for the next run of "Mail: Email Queue Manager", every five minutes on a single cron worker: measured in production, a median of 107 s between finalize and delivery, up to 327 s. `_wake_mail_queue()` wakes that cron right after queuing (`ir.cron._trigger()`, notified after the commit); on a bench with a real cron worker the mails leave in 2 s. Queuing is kept on purpose: sending SMTP inside the transaction would make the public finalize wait on the mail server and could deliver a link to a transfer that was rolled back. 3 new tests (finalize, resend, download notice), all three failing without the wake-up. |
| `18.0.1.21.0` | **Data-room presets — a link's configuration, named and replayed.** A data room is always set up the same way for a given kind of file (lifetime, visitor cap, per-person download budget, admitted domains, agreement), and redoing those choices by hand on every case is that many chances to get one wrong — in the one mode where a mistake **does not show**: a misconfigured link looks perfectly normal and refuses people **one at a time**, each in front of a form that talks about a code. New `secure.transfer.template` model, picked in the send wizard; three presets ship (**standard** 30 d / 25 visitors, **wide audience** 100 visitors with no join notice, **confidential** 7 d / 10 visitors / 3 downloads). ⚠ None carries a **brand**: the module is the single source for four tenants, and a brand seeded here would exist on none of them — brandless, the preset settles on whichever brand the sender already chose. ⚠ The fallback is **loud**: when the effective brand does not offer open audience (or SMS, or the agreement), the preset applies in part and the onchange **raises a warning** — a mode that drops silently back to "named recipients" is exactly the failure this model exists to prevent. Two constraints kill at save time what would have failed in production: open mode on a brand that does not offer it, and **SMS together with a domain list** (`_audience_admissible` already refuses mobile as soon as a list is set — a preset promising both lies to the sender). Extension point `_apply_vals()`, on the pattern of `_transfer_vals`. 525 tests. |
| `18.0.1.20.0` | **The open-audience mode gets its own menu entry.** The mode existed without being findable: it could only be picked by unfolding a radio in the send wizard, and that radio appears only once a brand offering the mode is selected, so an operator could open the wizard, see nothing, and conclude the feature did not exist. New **"New open-audience link"** entry, plus an **Open audience** filter and a **Delivery mode** group-by on the transfer list. ⚠ The default does **not** hold through the action context alone: `default_audience_mode` is wiped **silently** by `_onchange_brand_audience`, which runs at form load, finds no brand yet, and correctly concludes that an empty brand offers nothing. `default_get` picks a brand that *actually* offers the mode, and that choice is what makes the default survive the onchange. When no brand offers it, the wizard **refuses and says where to enable it** rather than opening a silent form in "named recipients" mode. 515 tests. |
| `18.0.1.19.1` | **Two defects surfaced by adversarial QA of 1.19.0.** *Redirect loop:* a session carrying the "code validated" flag with no identity in the database was handed to `_extra_access_gate`, which redirected to the very page that calls it. The browser looped; the secret held, but the person was locked in with no explanation. An inconsistent session is now **repaired**: the flag drops and the visitor redoes the only step that proves anything. A redirect, not a loop. *Policy leak:* the audience keys added to `_effective_limits()` were reaching the JSON config block of the public upload page, **rendered in clear to an anonymous visitor** — they state whether the tenant runs data rooms, whether SMS is wired, and how many visitors a link accepts. The page does not offer the mode and had no use for them: removed from the public block, kept on the back-office side. |
| `18.0.1.19.0` | **A link that names nobody — and, with the companion bridge, a signed agreement before entering.** *Open audience:* the historic mode requires knowing the recipients at send time; the one-time code only PROVES an already-listed identity. The new mode turns the question around — the visitor **declares** an email **or** a mobile, receives a code **on that channel**, and it is the confirmation that admits them. ⚠ The code never leaves on a channel other than the declared one, and a refused SMS therefore **does not fall back** to email (the named mode may fall back: the person is known in advance). Every admitted identity becomes a `secure.transfer.audience` row: its own **download budget** (`max_downloads` is global — ten visitors would exhaust it for the eleventh), its name **stamped on the PDFs** it downloads (the watermark could only print an IP as soon as there was more than one recipient), and it can be **blocked individually** without touching the link. *What keeps the link from becoming a relay:* a cap on **distinct** identities per transfer (0 refused from the wizard), a cap on codes per identity, a cooldown, and a domain allowlist falling back to the recipient allowlist — all held **in the database**, because the controller's sliding buckets live in process memory and are worth the number of workers. ⚠ A domain list can say nothing about a phone number: once set, mobile identities are **refused** rather than waved through. *Scope:* the mode opens from the **back office only** — offering it on the public form would let an anonymous visitor craft a link that sends DKIM-signed codes, and billed SMS, to addresses of their choosing. *Extension point:* `_extra_access_gate()`, an extra gate between the code and the content, called on the page **and** on the file route — wiring it to the page alone would let the direct file link through, which is precisely the one that circulates. |
| `18.0.1.18.0` | **Files can finally be attached to a send composed in the back office** — and the wizard stops being a way around its own rules. *Files:* the wizard only accepted a message; files had to go through the public page. They are now attached from Odoo (or from a contact's form), uploaded server-side, then **removed from the filestore**; an hourly cron sweeps the ones an abandoned composition leaves behind. ⚠ This is the **only** path in the module where bytes traverse Odoo: capped at 25 MB by default and **100 MB hard**; beyond that the answer is still the public page. *Guards replayed:* creating its transfer in `sudo()`, the wizard traversed **neither** the public create **nor** the finalize route — it therefore applied **no** allowlist, **no** quota, and let anyone pick any brand with any sender address. Now: **pinned identity** (only a manager composes under another identity, enforced server-side), sender allowlist, recipient allowlist, daily quota, drop pages refused, retention grid, password only where the brand offers it. *Abuse reporting:* the "Report abuse" link was nested inside the content branch of the template, so it only appeared **after** the password and the recipient code — a recipient of a back-office send never saw it. The form moved outside the gates. |
| `18.0.1.17.x` | **A transfer can finally say what it is.** New **`subject`** field, offered on the public form, the drop pages, the back-office send wizard and the record: it leads the mail subject, heads the body and titles the download page. Deliberately outside every gate — a header line travels in the clear, and that is the condition on which it can inform someone before they open anything — but kept out of the tab title, which the gate pages share with the content page. **Conditional `Reply-To`** so a reply reaches the human sender, except on a brand that accepts any sender, where removing that header would take away the only place abuse becomes visible. **Header hygiene**: `sender_name`, `subject` and `recipient_emails` are normalised in `create()`/`write()`. A CR/LF there is not header injection — Python's `email` stack refuses the value — it is a **silent delivery failure**: `mail.mail` lands in `exception` while the sender has read "transfer ready". **Two operator buttons**: re-send the link to the recipients (manager-only, no duplicate receipt), and extend the deadline — which reopens an expired transfer whose objects are still there, staying on the brand's retention grid because that grid is what the bucket lifecycle net is posted from. **Abuse desk resolved per tenant** (setting → company e-mail → the brand's sending address); no operator address is hardcoded anywhere any more. **The recipient code is bound to the session**, which the page and `SECURITY.md` now say out loud. 441 tests. |
| `18.0.1.16.x` | **The public pages became translatable — they never had been.** Odoo skips translation for an *entire* view whose arch starts with a doctype (`tools/translate.py`, `avoid_pattern`), so the three standalone pages exported **zero** translatable terms and an English visitor got a fully French page whatever the `.po` said, silently. The doctype is now injected at render time. Catalogue regenerated from a fresh export (694 terms). Also: the retroactive recipient-code action is now **manager-gated in Python** — a method without a leading underscore is an RPC surface, and the user group is read-only on `secure.transfer`. |
| `18.0.1.15.0` | **The share link no longer survives in the transfer's log.** The token is masked in the retained message bodies once the queue has delivered the mail, unless a recipient code guards the content; hourly sweep plus a post-send hook, and a migration that catches up on history. **Recipient code armable after the fact** from the backend (refused in link-only mode — nobody could receive the code), journaled. **Public form completed**: require-a-code, download budget and download notice existed only in the backend; the form now shows the instance-wide requirement as ticked and locked instead of absent. |
| `18.0.1.12.0`–`1.14.0` | **Coverage campaign — from 90 to ~400 tests**, with fixes the tests themselves surfaced: log-integrity appends now serialise through a `FOR UPDATE` on the parent row (an advisory lock could not work — Odoo runs in `REPEATABLE READ`, so every concurrent append read the same stale tail); `mpu_sign` accepted a string and iterated it character by character; orphan retention ignored archived brands, which could let the provider delete an object before its promised date. Retention guard extended to every ORM path, not just the public one. _(Catch-up release: intermediate versions grouped.)_ |
| `18.0.1.9.x`–`1.11.0` | **Access certificate (PDF)** and a **base review — 9 fixes**: the instance-wide recipient-code setting did not drive the e-mail template choice, so the gate held the files while the message body and the attachment listing still travelled in clear; the abuse notice went out as a single mail with every recipient in `To:` (third-party-triggerable disclosure); `_resolve_for_host` passed the `Host` as a LIKE pattern (`=ilike` does not escape `%`/`_`); "resend a code" reset the failure counter, so the attempt ceiling bounded nothing; a self-counting quota made a limit of 5 worth 4. _(Catch-up release.)_ |
| `18.0.1.7.x`–`1.8.0` | **Download watermark** promoted from a private downstream layer into the base (`watermark_downloads` per brand, with a migration that carries the setting over). SMS fixes (the provider answers **403 to urllib's default User-Agent** — the channel had never worked, silently) and S3 lifecycle fixes (the two rules were sent in a single call, so neither was applied). _(Catch-up release.)_ |
| `18.0.1.6.1` | **Localisation**: full en_CA translation of the module (fields, help, messages, send wizard, public pages) and of the "secure message" email. Fixed the email translation hook (terms containing markup are now applied correctly). |
| `18.0.1.6.0` | **Message only** mode: the body is **never again included in clear text** in the notification email — it carries only the link, and the message is read solely on the secure page (behaviour aligned with code-protected sends). |
| `18.0.1.3.0`–`1.5.0` | **Code-protected secure message for the recipient** (OTP delivered by email or SMS) plus a **backend send wizard**; sender-side send confirmation by code; **personal drop pages** auto-provisioned when an internal user is created; publishing a drop brand by its **slug** alone; hardened `LIKE` escaping in host resolution. _(Catch-up release: intermediate versions grouped.)_ |
| `18.0.1.2.1` | Fix: the default allowlists (`res.config.settings`) moved from `Text` to `Char` — a `Text` field on the settings crashed the whole Settings page (`_get_classified_fields`). The separator is commas. |
| `18.0.1.2.0` | Personal drop pages `/to/<slug>` (forced recipient); Files/Message-only tabs; sender email optional at drop time / required at send time; field order fix. |
| `18.0.1.1.0` | Email language following the Odoo contact (`partner.lang`); en_CA translations made durable through a migration hook. |
| `18.0.1.0.x` | Phase 2: burn-after-download, notify-on-download, paid custom domains, dashboard, billing, sender/recipient OTP, anti-piggyback allowlists, automatic suspension on abuse plus email notice. |
| `18.0.1.0.0` | MVP: direct S3 upload (simple + multipart), tokenised links, expiry/password, hash-chained Law 25 log, multi-brand, purge/crons. |


## Wizards are owner-scoped

Each wizard of this module carries a global record rule,
`[('create_uid', '=', user.id)]`, on read, write, create and unlink:

- `secure.transfer.add.file.wizard`
- `secure.transfer.extend.wizard`
- `secure.transfer.reveal.wizard`
- `secure.transfer.send.wizard`

A transient model is a real table, and Odoo 18 does not restrict it to its
creator on its own: without the rule, any internal user could read a
colleague's wizard until the autovacuum cron cleared it. The rule is global,
like core's own wizard rules, so administrators do not read others' wizard
rows either. (A file uploaded in a wizard before it is saved is stored as an
attachment with `res_id` 0: Odoo lets its uploader and system administrators
read it, whatever the rule.) Tests: `tests/test_wizard_owner.py`.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2030-11-28, this version converts automatically to
  **LGPL-3.0-or-later**.
