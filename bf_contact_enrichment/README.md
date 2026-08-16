# Contact enrichment (`bf_contact_enrichment`)

Cuts down manual data entry on `res.partner` records by enriching them
automatically from four sources. Reading business cards and email signatures
goes through the **`bf_llm`** gateway (provider configurable in *Settings ▸
Technical ▸ LLM Providers*); domain enrichment stays on the Claude bridge (web
search).

## Features

1. **Business card (OCR)** — *Contacts ▸ Enrichment ▸ Scan a card*, or the
   "Scan a card" button on a record. The image (JPG/PNG/PDF) is passed to
   `bf_llm` (`extract()`, vision), which reads the card and returns the contact
   details. The module detects an existing contact (email, name, domain) and
   offers to create or update; the card is attached to the record.
2. **Email signatures** — two buttons on the record: "Enrich now (signatures)"
   applies directly (fills blanks, one click), while "Enrich (review)" opens a
   field-by-field comparison. Both concatenate the correspondent's most recent
   incoming emails (`bf.email`, which mirrors IMAP, the gateway and the
   chatters) and send them to `bf_llm` (`chat()`, text). **In bulk**: *Contacts
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
   - **vCard import** (`.vcf`) — built-in parser, no external dependency.
   - **Duplicate detector** — by email and by normalised name; opens the subset
     for merging through the native Contacts action.
   - **Domain enrichment** — an "Enrich (website)" button: agentic web search
     (`/enrich/company`, WebFetch/WebSearch) that fills in the company. **The
     only feature still served by the bridge**: it requires autonomous web
     browsing, which is outside the scope of `bf_llm` v1 (single
     request/response). A future gateway version may absorb it.
   - **Completeness score** — a computed field plus an "Incomplete contacts"
     filter.

No populated field is overwritten by default (`_apply_contact_vals` only fills
blanks, unless the "Overwrite" option is set). Every enrichment is logged in the
record's chatter.

## Dependencies

`base`, `contacts`, `mail`, `bf_email_management`, `bf_llm`. Domain enrichment
additionally requires the bridge service (the `bf_claude_chat.bridge_socket`
socket).

## Privacy

The prompts (`CARD_PROMPT`, `SIGNATURE_PROMPT_HEADER`, supplied by `bf_llm`)
extract only what is actually present (never a surname guessed from the email
address) and ignore quoted history in emails. The content being read (cards,
emails) is treated as untrusted DATA, never as instructions.

## Changelog

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
