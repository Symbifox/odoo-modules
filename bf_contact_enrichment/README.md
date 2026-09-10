# Contact enrichment (`bf_contact_enrichment`)

Cuts down manual data entry on `res.partner` records by enriching them
automatically from four sources. Every AI call goes through the **`bf_ai_bridge`**
gateway — the local `claude-chatbot-bridge` socket, and behind it `claude -p`,
which means the tenant's own Claude subscription rather than a per-token API
key.

## Features

1. **Business card (OCR)** — *Contacts ▸ Enrichment ▸ Scan a card*, or the
   "Scan a card" button on a record. The image (JPG/PNG/PDF) is passed to
   the bridge (`/ocr/business-card`), which reads the card and returns the
   contact details. The module detects an existing contact (email, name, domain) and
   offers to create or update; the card is attached to the record.
2. **Email signatures** — two buttons on the record: "Enrich now (signatures)"
   applies directly (fills blanks, one click), while "Enrich (review)" opens a
   field-by-field comparison. Both concatenate the correspondent's most recent
   incoming emails (`bf.email`, which mirrors IMAP, the gateway and the
   chatters) and send them to the bridge (`/enrich/signature`). **In bulk**: *Contacts
   ▸ (list) ▸ Action ▸ Enrich from email signatures* queues the selected
   contacts, and a cron processes them in the background in batches (confidence
   threshold, never overwriting). A missing or misconfigured gateway degrades
   cleanly (a popup on the wizard side, an "error" status on the cron side,
   never an exception).
3. **Create a contact from an email** — a "Create / enrich the contact" button
   on a `bf.email` record: finds or creates the sender, then runs signature
   enrichment.
4. **Mobile scan page** (`/scan`) — the same card flow on a phone, as a
   standalone installable page rather than a native app. Scanning a card is a
   ten-second, foreground, occasional gesture: it needs no push, no offline
   store and no background work, so a page costs one install-free URL instead
   of a store listing. The page is a thin client — it re-implements neither
   extraction, nor duplicate matching, nor the write to `res.partner`; every
   route drives the same `bf.contact.card.wizard` as the desktop button.
   - Capture uses `capture="environment"`, so the phone's own camera app takes
     the photo and **the page never requests the CAMERA permission**.
   - The photo is downscaled to 1600px on its long edge before upload: an 8MB
     shot leaves as roughly 300KB, which matters on a conference-centre
     network. The model reads the small image just as well.
   - Access is checked at the door on **both** rights the flow needs. Holding
     the enrichment group is not enough: on a stock Odoo only
     `base.group_partner_manager` may create a `res.partner`, so a member
     without it could otherwise read a card — and pay for it — then hit a
     write refusal.
   - Installable: the service worker answers navigations offline with a fixed
     shell, which is what browsers require before offering to install. Nothing
     personalised is ever cached; the authenticated page always goes to the
     network.
5. **Quick wins**
   - **vCard import** (`.vcf`) — built-in parser, no external dependency. It
     reads the dialects real exports actually produce: Apple's group prefixes
     (`item1.TEL`), `QUOTED-PRINTABLE` accents from Android and Outlook 2.1,
     RFC 6350 escaping from Google, base64 photos. A work email wins over a
     personal one, a fax is not mistaken for a phone number, and a company
     already on file becomes the contact's parent instead of inheriting its
     employee's details.
   - **Duplicate detector** — by email and by normalised name; opens the subset
     for merging through the native Contacts action.
   - **Domain enrichment** — an "Enrich (website)" button: agentic web search
     (`/enrich/company`, WebFetch/WebSearch) that fills in the company.
   - **Completeness score** — a computed field plus an "Incomplete contacts"
     filter.

No populated field is overwritten by default (`_apply_contact_vals` only fills
blanks, unless the "Overwrite" option is set). Every enrichment is logged in the
record's chatter.

## Dependencies

`base`, `contacts`, `mail`, `bf_email_management`, `bf_ai_bridge`. Every AI
feature needs the bridge service to be running and its socket
(`bf_ai_bridge.socket`) mounted in the Odoo container, plus the system parameter
`bf_ai_bridge.tenant` naming this tenant. Without that parameter the call is
refused rather than guessed: a wrong tenant does not fail, it succeeds on
somebody else's subscription.

## Privacy

The prompts live on the bridge side, next to each endpoint, and extract only
what is actually present (never a surname guessed from the email
address) and ignore quoted history in emails. The content being read (cards,
emails) is treated as untrusted DATA, never as instructions.

## Changelog

- **18.0.2.1.0** — The scan page wears the tenant's accent colour instead of a
  hardcoded one. The page stays dark by design (it gets used at arm's length in
  trade-show lighting), so only the accent follows `report_brand_primary`, and
  the ink laid on top of it is computed from that colour's luminance rather
  than copied: the original near-black ink disappears the moment a tenant
  declares a dark brand. The field lives in a branding module that is not a
  dependency here, so it is read behind a guard, and only a `#rrggbb` reaches
  the stylesheet.

- **18.0.2.0.0** — Moved the card and signature reading off `bf_llm` and onto
  the `bf_ai_bridge` gateway, so extraction runs on the tenant's Claude
  subscription instead of an HTTP API key. `bf_llm` only speaks to keyed HTTP
  APIs, no tenant holds such a key, and its seeded provider ships disabled —
  so the `/scan` page and the enrichment buttons could not work anywhere. The
  tenant is now stamped by `call_bridge` from `bf_ai_bridge.tenant` and is no
  longer an argument a call site can get wrong: a wrong `org` does not fail the
  call, it bills it to another tenant's subscription. Test control moved down
  to the transport, and asserts on what leaves rather than on what comes back.

- **18.0.1.2.2** — Rewrote the vCard parser. Apple exports lost their email,
  work phone, address and website, all of them carried under `item1.`-style
  group prefixes; Android and Outlook 2.1 exports wrote `Fran=C3=A7ois`
  straight into the name; Google exports kept their RFC 6350 escaping and
  preferred a personal address over a work one. The parser now handles group
  prefixes, quoted-printable including its trailing-`=` line folding, RFC 6350
  escaping, `TYPE` parameters, base64 photos, notes, and UTF-16 or CP1252
  files, and an unreadable card is counted and skipped instead of aborting the
  whole file. A company already on file now becomes the imported contact's
  parent rather than inheriting its employee's email and job title. Nine tests.

- **18.0.1.2.1** — Added the installable mobile scan page at `/scan`: a
  standalone portal template (no `website` dependency, no backend chrome), two
  JSON routes driving the existing card wizard, a web app manifest and a
  service worker. The worker answers in-scope navigations with a cached offline
  shell — without that a browser refuses to call the page installable and
  degrades to a bookmark shortcut. Icons ship at 192 and 512 in both `any` and
  `maskable` purposes. First tests for this module (21 HTTP cases).
- **18.0.1.2.0** — Migrated the AI calls to the `bf_llm` gateway: business card
  → `extract()` (vision), email signatures → `chat()` (text). Added the
  `bf_llm` dependency. Domain enrichment stays on the bridge (agentic web
  search). Behaviour and JSON schemas unchanged; clean degradation when no
  provider is configured.
