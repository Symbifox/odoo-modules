# Contact enrichment (`bf_contact_enrichment`)

Cuts down manual data entry on `res.partner` records by enriching them
automatically from five sources. Every AI call goes through the **`bf_ai_bridge`**
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
4. **LinkedIn profile** — "Enrich (LinkedIn)" button on the record, or the
   *LinkedIn profile* source in the wizard. Two modes, because linkedin.com is
   closed to robots: any `/in/` or `/company/` URL answers **HTTP 999
   "authwall"** to a non-browser client, and its `robots.txt` forbids automated
   access. Fetching the page gets nothing.
   - **Search (free)** — `/enrich/linkedin` in `search` mode queries the public
     index (WebSearch) from the name, company and job title already on file, and
     reports the profile URL, title and employer read in the snippets. Namesakes
     that were set aside are listed under "Other profiles found". City and
     country are not reported.
   - **Reading a known profile (paid)** — `url` mode: RocketReach
     `/person/lookup`, keyed on the profile URL, which also reports the
     **professional** email and phone. **One credit per reading.** The "Read the
     profile" button on the review screen chains the two: the search finds the
     URL, the reading hooks onto it.

   The URL kept is stored in `x_linkedin_url` on the record and keys later
   readings. ⚠️ **A URL found by search must be checked by eye**: LinkedIn
   answers 999 to everyone, so a real profile and an invented one cannot be told
   apart from here, and only the shape of the URL is checked. That is what the
   review screen is for.
5. **Mobile scan page** (`/scan`) — the same card flow on a phone, as a
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
6. **Company completed by Gen** — when an incomplete company is created
   (no website, phone or street), including when it is picked from Odoo's
   autocomplete, the record joins a queue; the "Complete with Gen" button does
   the same on demand. A scheduled job calls `/enrich/company-search`: the
   bridge searches the company online by name and city (WebSearch) and, when it
   finds a domain while the address or phone is still missing, reads the site
   after the host check. Allow 20 to 60 s per company. The outcome follows the
   preference of the user who asked (*My profile ▸ Companies completed by
   Gen*):
   - **Propose the values to me** (default): the record shows "See Gen's
     proposal", which opens the current/proposed comparison; nothing is written
     without "Apply", and "Dismiss the proposal" closes it;
   - **Fill empty fields**: writes directly when confidence reaches 60,
     otherwise proposes;
   - **Do nothing on creation**: only the button remains.

   Below a confidence of 30, or with nothing usable, the record says "Nothing
   reliable found". The sources read are posted on the chatter with the name of
   the user who asked. Only members of the "Contact enrichment" group start a
   search (it uses the tenant's Claude plan); a programmatic creation (sudo,
   import, installation) never starts one. ⚠️ A chain with many branches comes
   out poorly, and Quebec's enterprise number (NEQ) never comes out: the
   Quebec registry is not in search engines' indexes.
7. **Quick wins**
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

### Third-party collection (18.0.2.3.0)

Email signatures and business cards come from the person themselves. LinkedIn
(search) and RocketReach (profile lookup) are **third parties**. From them the
module keeps **workplace contact details only**: name, title, employer,
employer website, profile URL. Email and phone come only from the
**RocketReach lookup**, and **only when the bridge tags them `professional`**
(`email_type`, `phone_type`); an address at a consumer provider (gmail,
outlook…) is dropped even when tagged. In search mode the tag is ignored: that
answer is written by a language model from web snippets. Never a mobile
number, a personal email, or the person's city, country or address; the review
screen hides those fields for a LinkedIn source.

The filter runs on receipt, before the answer is stored on the wizard: a
proposed field is already collected information, even if it is never applied.
A bridge that does not tag types lets neither email nor phone through.

Why this line: in Quebec, s. 1 par. 5 of the *Act respecting the protection of
personal information in the private sector* (P-39.1) excludes from Divisions
II (collection) and III (use, retention, communication, destruction)
information about a person's role within a business: name, title, and the
address, email and phone number of their workplace. Anything else, collected
from a third party, would in principle require the person's consent (s. 6).

Where each addition came from stays on the record's chatter ("Contact enriched
via <source>", with the list of fields).

Company completion by Gen (`/enrich/company-search`) only targets `is_company`
records, and the prompt asks the model to report no person's name, title or
mobile number.

## Changelog

- **18.0.2.5.0** — The source is now written in English; French lives in
  `i18n/fr_CA.po` (Odoo never translates *to* `en_US`, so a French source read
  French for every user set to English). The Gen proposal window carries its
  title ("Enrich a contact", it opened as "Odoo") and lists the sources it read
  as links under a heading, instead of an unlabelled text box that showed one;
  the "Overwrite existing values" box no longer hides its label. What the
  scheduled job writes (status message, notification, chatter note) is now in
  the language of the user who asked, not of the job's user. Every sentence of
  the scan page's script, which cannot be translated, now travels with the page;
  the page's title, language and web app name follow the reader, and the cache
  of the installed page is refreshed from the network (Odoo serves `/static/`
  for a week). Countries are looked up in English first, then in the reader's
  language, and a name no longer matches a country *code* (the code is
  truncated to two characters: "États-Unis" used to find Ethiopia); common
  abbreviations ("USA", "UK", "CAN") are mapped. The duplicate finder words its
  reasons for the reader, and the field names in the chatter summary are
  translated.

- **18.0.2.4.0** — Companies completed by an online search by Gen: queue on
  creation and a button, a scheduled job, a per-user preference (propose by
  default, fill, start nothing), sources on the chatter. Needs a bridge with
  `/enrich/company-search`; without it the record shows "Gen service
  unavailable".

- **18.0.2.2.1** — The enrichment summary posted on the chatter was an ordinary
  string, which `message_post` escapes: the one contact ever enriched showed its
  tags in the clear. It is now real HTML, with the substituted values still
  escaped.

- **18.0.2.2.0** — The two lineages meet. The Blue Fox line (1.5.x) brings the
  LinkedIn enrichment; the published line (2.x) brings the tenant stamped by the
  bridge (no call site carries a tenant name any more) and the tenant's accent
  colour on the `/scan` page.

- **18.0.2.3.0** — Third-party collection: the LinkedIn lanes keep workplace
  contact details only, filtered on receipt; email and phone pass only from the
  RocketReach lookup, tagged `professional` by the bridge. vCard import: a URL's
  host is read with `urlparse` (exact domain or subdomain, `\` read as `/`,
  http and https only) instead of a substring match (CodeQL #8).

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
