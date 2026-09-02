# Changelog — `bf_sign`

Versioning follows the Odoo `18.0.MAJOR.MINOR.PATCH` convention.

## 18.0.3.23.0 — The company logo comes first on every portal page

The three portal templates each build the header image from the same fallback,
and they had drifted apart: `verify_page` had been switched to prefer
`res.company.logo` while `sign_page` and `sign_otp` still preferred
`report_brand_logo`. A signer opening the document and a third party checking
its signature would see two different marks on the same envelope.

All three now read `logo and 'logo' or 'report_brand_logo'`. The company's own
logo is the identity a signer is being asked to trust; `report_brand_logo` is
the branding pack's print mark and stays the fallback for a company that has
not uploaded a logo.

## 18.0.3.22.0 — Sealing key material is administrator-only

`bf.sign.seal` is an `AbstractModel`. It has no table, so no `ir.model.access`
row can ever apply to it — yet its public methods are dispatched over RPC like
any other model's. `fernet_key_source`, `store_fernet_key`,
`action_generate_fernet_key` and `action_generate_cert` read or write the
sealing key material and had no rights check of their own, which left them
reachable by any authenticated user over `call_kw`.

- They now call `_require_admin()` (`base.group_system`) first.
- `seal_pdf`, `verify_pdf` and `has_cert` stay open on purpose: they run inside
  the signing flow, where the environment belongs to a portal or public signer.

The gate shipped in this distribution first; it had never been carried back into
the private lineage of the module, which ran without it until now.

## 18.0.3.21.0 — The audit trail now matches what was actually emailed

Found in production: the second signer of a sequential request was invited by
email, and the journal said nothing about it. Two defects, pulling in opposite
directions.

### The sequential hand-off is journalled
- When a signer completes a sequential request, `_post_sign_progress()` emails
  the next signer in line. That call was the only one of the five `_email_signer`
  call sites with no `bf.sign.log` entry behind it — the cron reminder, the
  manual request-level reminder and the per-signer resend all wrote one.
- The journal therefore showed a signature followed by nothing, and the sole
  trace that the next signer had been reached was their `invited_on` field: not
  part of the chained, immutable trail, and worth correspondingly less if the
  signature is ever contested.
- A `sent` entry is now appended for the hand-off, actor `system`.

### `action_send` no longer claims sends that did not happen
- The `sent` entry built its note from every signer's email
  (`signer_ids.mapped("email")`), while in **sequential** mode `action_send`
  emails only the signer whose turn it is. The trail asserted invitations to
  people who had received nothing.
- The note is now built from the signers actually mailed, and says how many
  remain to be invited in turn. The entry is also written **after** the mails go
  out rather than before.

## 18.0.3.20.0 - Signing a document that already exists

### `_sign_document_file()` on `bf.sign.mixin`
- The mixin could only put a **freshly rendered QWeb report** under a signature.
  That is right for a quotation, which *is* its report, and wrong for a record
  whose document already exists as a stored PDF. An approved policy is the file
  that was approved; re-rendering it at send time would put a different document
  under the signature than the one the approval round agreed to.
- Concrete models may now return base64 PDF bytes from `_sign_document_file()`.
  When they do, `_sign_report_ref()` is not consulted. Returning `None`, the
  default, keeps the previous behaviour exactly, so every existing bridge
  (`bf_sign_sale`, `bf_sign_purchase`, `bf_sign_corporate`, `bf_sign_privacy`,
  `bf_sign_onboarding`) is untouched.
- First consumer: `bf_sign_document`, which signs a distributed document version.

## 18.0.3.19.0 — Reaching the verification page without a QR

### The certificate now carries the pointer to its own proof
- The QR on the document pages is optional and **off by default**, so a holder
  could be handed the evidence with no way to find the page that checks it. The
  **completion certificate** now prints the verification URL and a QR — and the
  certificate is bound into the signed bundle by default, so the pointer travels
  with the document either way.
- The QR is embedded as a `data:` URI rather than a `/report/barcode` URL, so the
  render does not depend on wkhtmltopdf calling back into the server over HTTP —
  the part of certificate rendering that is already the most fragile.
- `verify_qr_data_uri()` returns False instead of raising if the generator
  fails. A certificate is evidence; a broken QR must not be why one fails to
  render.
- **Existing signed documents are untouched.** This changes what future
  certificates say, nothing that has already been sealed.

### Sharing the link for documents already in the wild
- **"Partager le lien de vérification"** on the Proof tab opens a composer
  prefilled with the link and a plain-language explanation.
- It **refuses to mint a token on the fly**. A request finalized before the
  verification page existed carries none, and creating one would quietly alter a
  signed record to make a button work. The form says so instead, and points at
  "Vérifier l'intégrité", which works regardless. Pinned by a test that asserts
  no token appears after the guard fires.

### Dead code that was one edit away from a silent no-op
- Removed **94 lines** duplicating `_notify_source_signed`, `_signed_filename`,
  `_stamp_document` and `_draw_field`. Python bound the later copy, so behaviour
  was correct — but the earlier, dead copies were stale ancestors (no QR
  stamping, no checkbox or extended pad types), and editing one would have had
  no effect at all. An AST check now confirms zero duplicate definitions.

### The stamped QR is a link too

- **The verification QR is now a clickable area** covering the whole card
  (code plus caption). Documents are read on a screen far more often than on
  paper, and holding a phone up to a monitor to scan a code you could have
  clicked is a poor way to check a signature. The QR itself is unchanged, so
  the printed path still works exactly as before.
- Implemented as a reportlab link annotation on the stamping canvas. That
  annotation **is** carried through PyPDF2's `merge_page` (verified rather than
  assumed — the overlay is a throwaway canvas merged into the real page, so it
  was not obvious), which is why no annotation has to be rebuilt on the
  destination page afterwards.
- Pinned on the **sealed** path, not just the plain one: pyHanko rewrites the
  document to embed the PAdES signature, so the test generates a sealing
  certificate, asserts `sealed`, finds the URI in `/Annots`, and re-verifies the
  seal with the annotation present. An unsealed-only test would have proved
  nothing about production, where every tenant seals.

## 18.0.3.18.0 — Verify page: compare your own copy

### The fingerprint stops being homework
- The public verification page printed the SHA-256 and told the holder they
  "can compute it themselves". Almost nobody can, so the one check that speaks
  about *their* file — rather than about the copy we keep — went unused.
  The page now takes a drop zone: pick or drag the PDF, get a plain verdict.
- **The file is hashed in the browser** (WebCrypto `crypto.subtle.digest`) and
  never sent anywhere. That is not a detail: an upload endpoint on a public
  route would have to be authenticated, rate-limited, size-capped and wiped,
  and the page would lose the "no file is uploaded here" promise it makes two
  paragraphs above. Client-side hashing gives the same answer with no intake.
- A mismatch says what a mismatch actually means. The comparison is
  byte-for-byte, so a PDF re-saved by a viewer, a scan of a printout, or the
  certificate delivered as a separate file all differ legitimately — the
  message names those cases before pointing at tampering.
- Falls back cleanly: no WebCrypto (plain HTTP, old browser) hides the drop
  zone rather than swallowing the file in silence, and the `shasum` /
  `Get-FileHash` commands are spelled out on the page either way.
- Files over 100 MB are refused without being read — a signed bundle is a few
  megabytes, and the read would otherwise freeze the tab.

## 18.0.3.17.1 — Audit: in-flight safety and hardening

### Requests already sent are left alone
- **Reminders stay OFF on anything already in flight.** `reminder_enabled`
  defaults to True, so on upgrade every open request would have become eligible
  and started chasing counterparties because we deployed. A post-migration
  (`18.0.3.16.0`) disables reminders on requests already `sent`/`in_progress`;
  the feature applies to new requests, and a preparer can turn it on for a
  specific old one deliberately.
- `invited_on` is backfilled from the journal for those requests all the same:
  the information is true and shows in the Recipients list, and it stays inert
  while reminders are off.
- Verified against the two requests actually in flight across the estate: every
  pad carries `sequence=10` (so the sequence-first ordering is a no-op), no pad
  carries a `value_text` (so the blank-label fix cannot change what is stamped),
  every pad is required, and `auto` appears only on date pads (so the new
  constraint has nothing to reject). Regression tests pin all four.

### Hardening
- The verification token is compared with `hmac.compare_digest`, like the signer
  token, instead of an equality search on an indexed column.
- Manual reminders are **debounced to one per signer per hour**, on both the
  request-level and per-signer buttons. Both are RPC-reachable by any sign user,
  and neither was rate-limited: the cap only governed the cron.
- "Renvoyer l'invitation" becomes "Relancer ce signataire" and now sends the
  reminder template — matching what a resend on an in-flight request actually is.

### Publishability
- README brought up to date across the four lots; it described none of them.

## 18.0.3.17.0 — Public verification, QR on the document, RFC 3161

### A page anyone holding the document can reach
- New public route `/sign/verify/<id>/<token>`. Until now every route was tied
  to a signer token, so there was no way for a third party — a bank, a
  registrar, a buyer — to check that a signed PDF really came from here.
- The page states what a holder can already see (reference, title, signing date,
  signer **names**) plus what they cannot check on their own: the integrity
  proofs, **recomputed at each visit** rather than read from a stored verdict.
- It discloses **no email address** and **never serves the document**. The
  SHA-256 imprint is displayed so a holder can compute their own copy's digest
  and compare — nothing is uploaded, nothing is stored.
- `verify_token` is minted at finalize for **every** signed request, QR or not,
  so the link can simply be shared. It is deliberately not a signer token: it
  opens a read-only page and can never sign anything. Unknown tokens fall on a
  neutral page and count against the existing per-IP rate limit.

### QR on the document itself
- Optional `verify_qr`, off by default, with a choice of corner and of pages
  (last / first / all). ⚠️ It is stamped **over** the content: an opaque backing
  keeps it scannable, but the corner has to be chosen on a real document.
- Drawn as **vector geometry**, not a rasterised image: a QR printed from a PNG
  at PDF scale blurs at the module edges, which is what makes a scanner give up
  on paper.
- Stamped by the same pass as the pads, so a request with no pad at all still
  gets its QR.

### RFC 3161
- The setting existed but was undocumented and off; it now states the trade-off
  plainly — what it buys (the signing date stops resting on our own clock) and
  what it costs (an outbound call at finalize, logged and non-blocking on
  failure) — and the TSA URL is editable beside it.

## 18.0.3.16.0 — Reminders

A signature request had no follow-up of any kind: one cron (expiry), three
templates (invitation, completed, refused). A signer who ignored the first
email was never chased again.

### Schedule
- A daily cron reminds signers who have not signed, **counting from the moment
  that signer was invited** — not from the send. In a sequential request the
  second signer is invited days later, and chasing them on the first signer's
  clock would be nonsense. `invited_on` is stamped when the invitation actually
  goes out.
- Defaults: **J+3, J+7, then a last call 48 h before the link expires**, capped
  at **3 reminders per signer**. All four values are settings, and the whole
  thing is switchable per request (`reminder_enabled`).

### Guards — a request that nags is worse than one that is forgotten
- One reminder per signer per day maximum, whatever the schedule says.
- Never a signer who has signed, refused, or whose turn has not come in a
  sequential request: a link they cannot use reads as a broken system, and it
  discloses the request to a party out of turn.
- Never an expired or disabled request. Every reminder is journalled.
- A reminder does **not** restart the signer's clock, only a genuine invitation
  does (`_email_signer(mark_invited=…)`).

### Never-opened alert
- When a signer has not **opened** the document after 5 days (a setting), a note
  is posted once on the request. A signer who opened and did not sign is
  hesitating; one who never opened usually means the mail is not arriving, which
  needs a human, not another automated copy of the same message.

### Manual
- "Relancer les signataires" **replaces "Renvoyer les liens"** in the header.
  The old button re-ran the whole send, which re-mailed everyone — people who
  had already signed included. The new one targets only signers who may sign
  right now.

## 18.0.3.15.0 — Editor comfort, presentation order, working duplication

### Placement editor
- **Snapping and the grid overlay are now two separate settings.** The ruled
  overlay is visually loud and most of the time you want the magnetism without
  seeing it, so the grid is **off by default** while snapping stays on. Alt
  still suspends the magnetism entirely.
- **Duplicate a pad** — a button on the properties bar, or Ctrl+D. The copy is
  offset below the original and clamped inside the page.
- **Reorder the fields as the signer sees them.** Each pad carries the rank the
  signer will be shown, and the properties bar moves it up or down.
  `_overlay_fields()` now sorts on `sequence` first and falls back to reading
  order (page, top, left), so untouched requests are ordered exactly as before.
  The editor asks the model for that order (`get_field_order`) instead of
  re-deriving the sort in JavaScript, so the numbers shown while placing cannot
  drift from the numbers printed on the signing page.

### Preparer comfort
- **Resend the invitation to a single signer** (`action_resend_invitation`),
  instead of re-running the whole send and mailing everyone again — people who
  had already signed included. Gated on `_signer_can_sign`, so a sequential
  request never invites someone out of turn, and the resend is journalled.
- Fillable inputs carry `maxlength` from `bf_sign.max_field_chars`. The server
  already rejected anything longer, but only at submit — after the signer had
  drawn their signature and pressed Sign.

### Fixes
- **Duplicating a signature request no longer fails.** `field_ids` and
  `signer_ids` are both copyable, but a pad references its signer by id: copying
  the two lists side by side left every new pad attached to the ORIGINAL
  signers, which `_check_signer_request` rejects. Duplicate — the standard Odoo
  action included — raised a validation error. `copy()` now rebuilds the pads
  against the new signers. Signing tokens are still never inherited.

## 18.0.3.14.0 — Snap-to-grid placement, new pad types, preset values

### Placement editor
- **Magnetic grid**: an optional grid overlay (4/8/12/16/24 px) that pads snap to
  when placed, dragged or resized. **Alignment guides** additionally pull a pad
  onto a neighbour's edge when it comes within a few pixels. Hold **Alt** to
  suspend every aid for a pad that has to sit off-grid.
- **Continuous placement**: the toolbar stays armed after a pad is dropped, so a
  row of pads no longer costs one toolbar round trip each.
- **Optimistic placement**: a pad is drawn as soon as it is clicked instead of
  after the server confirms it, and is rolled back if the write fails.
- **Keyboard**: arrows nudge the selected pad by one grid step, Shift+arrows by
  one pixel, Delete removes it, Escape disarms.
- **Pad properties bar**: the selected pad's signer, fill mode, value/label and
  required flag are editable in place. The signer is therefore assignable
  **after** the pad is dropped, instead of having to be chosen beforehand.

### New pad types
- `name`, `email`, `number` and `checkbox` join `signature`, `initials`, `date`
  and `text`, on the placement editor, the signing page and the stamping engine.
- `fill_mode='auto'` is **generalised**: it resolved the signing date only, it
  now also resolves the signer's name and email. A constraint rejects `auto` on
  a type the system cannot resolve.
- Signer input is validated per type: numeric for `number` (thin, narrow and
  non-breaking spaces tolerated), shape check for `email`, and a required
  checkbox must actually be ticked.

### Preset values
- `value_text` (the fixed value set by the preparer) had **no input anywhere in
  the placement editor**: choosing "fixed" produced a pad that stamped nothing.
  It is now editable on the selected pad, as is `required`.

### Document opening, visible from the list
- A signer's openings are recorded on the signer itself — `first_viewed_on`,
  `last_viewed_on`, `view_count` — instead of only in the audit trail. Answering
  "has this person even looked at it" no longer means reading the journal.
- The request carries the rollup: `viewed_count`, `last_viewed_on` and a
  `view_status` badge (not yet opened / partly opened / opened by all), shown as
  columns on the request list.
- The rollup counts `first_viewed_on`, **not** the signer `state`: a signer who
  has signed left the `viewed` state behind and would otherwise read as never
  having opened the document.
- **Migration** `18.0.3.14.0/post-migrate.py` backfills the timestamps from the
  `viewed` entries of the append-only journal, matching on request + signer
  email, so requests predating the upgrade do not all read as "never opened".
  A signer whose state proves an opening but whose journal line cannot be
  matched is flagged without a date being invented.

### Signed document without the certificate
- New `append_certificate` option on the request, with a matching default in
  Settings. Unchecked, the signed document is delivered on its own instead of
  having the certificate bound to its last pages.
- **Evidence is unchanged either way**: the certificate is still rendered,
  attached, emailed and covered by the same PAdES seal — it simply stays a
  separate file. `hash_signed` covers whatever the signed attachment contains.

### Fixes
- **A pad left blank by its signer no longer stamps its own label.** `value_text`
  doubles as the caption in signer mode, and the stamping engine fell back to it
  when `filled_value` was empty — printing "Employee number" on the signed
  document instead of a number. Value resolution is now driven by `fill_mode`
  alone.
- **Sending is blocked when a signer has no pad** while other pads exist: they
  would have received a signing page with nothing to sign, and left no visible
  mark on the document. A request with no pad at all (seal only) stays valid.
- Stamped text is **fitted to its pad** instead of a fixed 9 pt, and shortened
  with an ellipsis rather than bleeding across the page. Resizing a pad in the
  editor now actually changes the stamped size.
- The fields to fill on the signing page follow **reading order**, so their
  numbers ascend the same way as the badges drawn on the document.

## 18.0.3.13.2 — Removing the "advanced signature (AES)" option

- **The "LibreSign — advanced signature (AES)" choice is removed** from
  `signature_method`. The option was selectable in the form but had **no
  implementation**: the request went through the ordinary SES pipeline whatever
  the choice. Since "advanced" has a precise meaning in electronic signature
  vocabulary, the gap between what the UI promised and what the module delivers
  was a misrepresentation problem.
- The field remains (unchanged structure, a single `native_ses` value) and
  becomes **read-only**: it states the signature level obtained instead of
  offering a choice between two tiers of which only one exists.
- **Migration** `18.0.3.13.2/pre-migrate.py`: resets to `native_ses` any request
  still carrying `libresign_aes`.
- Docs: the manifest and the README no longer present the field as "preparing an
  AES tier through LibreSign".

## 18.0.3.13.1 — Python dependencies actually declared

- `external_dependencies` declared only `PIL`, `reportlab` and `PyPDF2`. The
  PAdES sealing (`pyhanko`, `pyhanko_certvalidator`, `asn1crypto`,
  `cryptography`) and the timestamping client (`requests`) were imported without
  being declared: a host missing those packages installed the module without
  warning and failed at signing time. All are now declared, so a missing package
  is detected at installation.

## 18.0.3.13.0 — Moving to the Business Source License 1.1

- The licence moves from **LGPL-3** to **BUSL-1.1**. Production use for your
  **own internal business operations** remains allowed without an agreement;
  providing the module as a product or service to third parties (hosted, managed
  or resold) requires a written agreement with Les services de consultation Blue Fox, Inc. On **2029-07-20**,
  this version converts automatically to **LGPL-3.0-or-later**. See `LICENSE`.
- `THIRD-PARTY.md`: attribution for the embedded SIL OFL typefaces (*Caveat*,
  *Dancing Script*, *Great Vibes*), which stay under their own licence and are
  not covered by the BUSL.

## 18.0.3.12.0 — A friendly title plus embedded signature fonts

- **A "Title" field** on the request: a friendly name for spotting a document in
  the list (the `SIGN-YYYY-NNNN` reference remains the unique key). Shown as a
  list column, in the record header, in kanban, searchable, and reused in the
  `display_name` ("Title (SIGN-2026-0001)") and on the signing page.
- **Embedded signature fonts (SIL OFL)**: the "Handwritten / Cursive / Elegant"
  styles pointed at system fonts absent from most devices, so they all looked
  alike. Three real fonts are now **self-hosted** in the module — *Caveat*,
  *Dancing Script*, *Great Vibes* — and applied to both the typed name and the
  typed initials, rendering identically on every device. The canvas waits for
  the font to load before drawing. The OFL licences are included in
  `static/fonts/`.

## 18.0.3.11.0 — Uploading a signature/initials image

- **A third "Upload" mode** on the signature and initials fields, alongside
  "Draw" and "Type": the signer can pick a **PNG or JPG** file (a scanned
  signature, for instance). The image is fitted and centred in the zone, then
  handled like the other modes.
- **No change to the pipeline or the storage format**: the uploaded image is
  drawn on the canvas and re-encoded as PNG (`canvas.toDataURL`), so it goes
  through the same validation (PNG, maximum size), the same stamping and the
  same audit trail. A JPG is converted to PNG automatically, client-side.

## 18.0.3.10.0 — Typed initials plus values derived from the signer

- **Initials can be typed.** The "Your initials" field now offers the same
  **Draw / Type** choice as the signature: you can type your initials (in a
  handwritten/cursive/elegant style) instead of having to draw them, which was
  awkward on a trackpad.
- **Values predetermined from the signer's name.** In "Type" mode, the initials
  field is prefilled with the initials derived from the signer's name ("Marie
  Tremblay" → "MT"), and the signature's typed name stays prefilled with the
  full name. Both remain editable.
- No change to the stamping pipeline or the audit trail: typed initials produce
  the same PNG image as drawn initials.

## 18.0.3.8.3 — A logo for dark backgrounds in the headers

- The dark headers (branded emails plus public signing pages) use the company's
  **dark-background logo** (`report_brand_logo`, a new `bluefox_branding` field)
  when it is set — typically the white version of the logo — otherwise the
  standard logo. This avoids a dark logo being invisible on the dark band. (The
  3 `noupdate` email templates are recreated through a migration to apply the
  change.)

## 18.0.3.8.1 — The default company is the creator's main company

- The signature request defaults to the creator's **main company** (rather than
  the active company from the multi-company selector), so the document is always
  branded (colours plus logo) by the primary organisation even when another
  company is selected. The field stays editable while in draft.

## 18.0.3.8.0 — Hardening (pre-publication audit)

### Security
- **The signing token no longer travels through a persistent message**: the
  invitation is sent as a standalone email (with no document link, auto-deleted),
  so a signature user can no longer retrieve a signer's token from a
  `mail.message` body and sign in their place. This restores the "no signing on
  someone else's behalf" guarantee from 18.0.3.4.0.
- **Record rules** on signers / fields / log: a user only sees those of the
  requests they can see (no more reading the signers, fields and audit trails of
  other creators or companies).
- **Posting back to the source record restricted**: at finalisation, the signed
  document is posted back only if the request's creator genuinely has write
  access to the source record (no more `sudo` writes to an arbitrary
  `res_model` / `res_id`).
- **A cap on OTP resends** (anti email bombing) on top of the 30 s delay between
  sends.

## 18.0.3.7.0 — Experience refinements

### Emails
- **The header title sits to the right of the logo** (rather than below it),
  aligned with the other Symbifox emails — invitation, completion, refusal and
  OTP code.

### Security — revealing the link
- **"Copy link" in two steps**: revealing a signer's link first shows a
  **warning**; the link is exposed — and the reveal written to the audit trail —
  **only after** the manager explicitly confirms (cancelling reveals nothing and
  logs nothing).

### Signing
- **A typed signature** as an option on the signing page: the signer can **type
  their name** (in several styles) instead of drawing it — drawing remains the
  default.

## 18.0.3.6.0 — Code verification (OTP) at signing time

- **An optional email OTP** (off by default, enabled globally or per request):
  the signer enters a 6-digit code sent to their email **before** viewing and
  signing — proof of inbox control at the moment of signing. A time-limited
  code, an attempt cap, and the `/document` and `/submit` routes blocked until
  verified. The identity method recorded becomes `email_otp`.

## 18.0.3.5.0 — Sending for signature from other modules

- **The `bf.sign.mixin` mixin**: a "Send for signature" button plus a smart
  button on any model, with customisation points (report, signers, filename).
- **The `bf.sign.request.create_from_record` factory**: renders a record as a
  PDF and creates a **linked** request (`res_model` / `res_id`).
- **Posting back to the source record**: on full signature, the signed document
  plus the certificate are copied onto the source record and a note is published
  in the thread (no state change).
- **Bridge modules**: `bf_sign_sale` (quotations / orders) and
  `bf_sign_purchase` (purchase orders).

## 18.0.3.4.0 — Integrity of the signer's link

- **The signing link / token is no longer exposed to the requester**:
  `access_token` and `signing_url` are restricted to managers — a user can no
  longer copy a signer's link and sign in their place. The invitation is still
  sent normally (rendered under `sudo`).
- **A "break glass" reveal** by a manager, **written to the audit trail** (the
  `link_revealed` event).

## 18.0.3.3.0 — The encryption key through the UI, plus fixes

### Configuration
- **A self-service Fernet key**: an administrator can **generate or import** the
  sealing certificate's encryption key from *Settings* (stored in the database);
  a key set in `odoo.conf` / the environment keeps precedence. The trade-off is
  documented in `SECURITY.md`.

### Emails
- **An action title** added to the email headers (invitation / completion /
  refusal).

### Performance (the placement widget)
- **PDF rasterisation decoupled from data reloading** plus a page cache:
  changing a field type, saving, or applying a template no longer re-renders the
  whole document.

### Public signing page
- **A document display fix**: since Odoo ships pdf.js as an **ESM** build,
  loading it as a classic script failed silently → loaded through a dynamic
  `import()`.

## 18.0.3.2.1 — An OWL crash fix

- Fixed a crash in the placement widget (`ctx.String is not a function`):
  `String(...)` is not exposed in the OWL template context.

## 18.0.3.2.0 — Placement markers on the signer's side, plus structural locking

### Signing experience
- **A rendered preview of the document** on the signing page (PDF.js → one image
  per page) replacing the raw `<iframe>`, with **numbered placement markers**:
  each of the signer's fields (signature, initials, text, date) appears at its
  exact position on the document, with a **clear identifier** (a numbered badge
  in reading order: page, then top→bottom, left→right).
- **Cross-referencing**: the headers of the "Your signature / initials" cards and
  each field to fill carry the same number as the matching marker. Touching a
  marker takes you to the associated field or card.
- **A live mirror**: the drawn signature/initials and the typed text/date values
  appear in the markers as you type (the marker turns green once filled). A
  `<noscript>`/error fallback offers a link to open the PDF.

### Integrity — structural locking
- **Recipients and fields frozen outside draft**: once the request is sent (or
  signed), it is no longer possible to add, modify, move or remove a **signer**
  or a **field** — enforced at model level (`create`/`write`/`unlink`) on
  `bf.sign.signer` and `bf.sign.field`, with an *allowlist* of the fields the
  signing flow still needs to write (`filled_value`, images, consent, state, and
  so on). The escape hatch is "Reset to draft". Reflected in the form (read-only
  plus a banner) and in the layout widget (placement disabled outside draft).

## 18.0.3.1.0 — Widget fixes plus field templates

- **The background document's display fixed** in the placement widget
  (off-screen render → image, no more canvas synchronisation problem) and
  vertical compression fixed.
- **Reusable field templates** (`bf.sign.field.template`) storing the layout
  **per signer rank**; a "Template" bar (apply / save) in the widget.

## 18.0.3.0.0 — The PAdES digital seal

- **A digital seal** (pyHanko): an invisible cryptographic "organisation"
  signature on the final document → a PDF reader (Adobe and others) shows
  "signed / not modified" (DocuSeal style). A self-signed X.509 certificate
  generated from the settings, **Fernet-encrypted** in `ir.config_parameter`
  (with the key outside the database through `odoo.conf` / the environment).
  Integrity verification extended to the seal.

## 18.0.2.1.0 — Hardening and finishing of tier 1

### Security
- **Input validation**: signature/initials images are validated (PNG format,
  size capped, integrity through Pillow) **before** the signer is marked signed,
  and the uploaded document is validated (PDF, size, unencrypted, readable) at
  send time. The caps are configurable (`bf_sign.max_signature_kb`,
  `bf_sign.max_document_mb`).
- **A double-finalisation guard**: a row lock (`SELECT … FOR UPDATE`) plus
  re-reading the state at the top of `_finalize`, to serialise two concurrent
  "last signer" submissions (no duplicate attachments and no duplicate email).

### Integrity & evidence
- **RFC 3161 re-anchoring**: the timestamp token now covers the **signed
  content** (a new `hash_stamped` hash) and is obtained **before** the
  certificate is rendered, so the certificate **actually shows** the timestamp
  and the attested time (`tsa_gentime`). The TSA timeout is brought down to
  10 s.
- **One-click integrity verification** (`action_verify_integrity`): recomputes
  the log chain, the sealed document's hash and the RFC 3161 token's match; the
  result is displayed and recorded in the chatter.

### Features
- **A refusal flow**: the public `/sign/<id>/<token>/refuse` route, a button and
  a reason on the signing page, a confirmation page, and a notice email to the
  requester.

### UX
- **A more robust placement widget**: PDF.js errors surfaced, a "Reload" button,
  and a guard preventing placement while the form has unsaved changes (since the
  fields are written immediately).

### Publication
- Python `external_dependencies` declared in the manifest (`PIL`, `reportlab`,
  `PyPDF2`).
- The manifest description corrected (multiple signers).
- A test suite (`tests/`) covering the life cycle, validation, parallel and
  sequential signing, finalisation idempotence, log immutability, refusal,
  expiry and integrity verification.
- `README.md`, `CHANGELOG.md` and `SECURITY.md` added.

## 18.0.2.0.x — Multiple signers and placement

- Multiple signers (parallel / sequential) through `bf.sign.signer`.
- Visual field placement (`bf.sign.field`) by drag and drop (OWL widget plus
  PDF.js) and a stamping engine (reportlab plus PyPDF2).
- A branded completion certificate, SHA-256 hashes, a chained append-only trail,
  optional RFC 3161 timestamping.

## 18.0.1.0.0 — Initial tier 1 (SES)

- The `bf.sign.request` / `bf.sign.log` models, a tokenised public controller,
  a drawn signature, a QWeb certificate, an immutable log.
