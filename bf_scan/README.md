# Scan from your phone (`bf_scan`)

`bf_contact_enrichment` already serves a standalone, installable page at
`/scan`: point the phone at a business card, correct what was read, save the
contact. Ten seconds, one hand, no app to install.

This module keeps that page and adds the two other pieces of paper that end up
in a pocket.

## What it adds

| Tile | What the photo becomes | Who sees the tile |
|---|---|---|
| Business card | a contact (unchanged, served from `/scan/carte`) | contact enrichment group + the right to create a contact |
| Invoice | a **draft** vendor bill, the photo attached in its thread | the billing group + the right to create a bill |
| Document | a note in the **`bf.note` pad**, with a reminder that becomes an activity — or straight into the thread of any record | any internal user who may create a note |

`/scan` itself becomes the home screen: three tiles, and a tile only shows up
when the person may actually perform that gesture.

## What it does not do

* **It does not read.** No prompt, no gateway call. Reading a bill belongs to
  `bf_invoice_ocr`, and a photographed bill is left at `ocr_state = none` so
  that module's hourly pass picks it up the day it can read images. Marking it
  `error` today would hide it from that pass forever.
* **It does not guess the record.** The picker goes through
  `bf.chatter.target`, which only ever returns records the person may read.
* **It does not widen a right.** Each tile is gated on the right of the gesture
  it performs — and the gate is the model's create right *and* the group, so a
  person who can create a bill through some other group still does not get a
  billing page.
* **It does not trust the file name.** The type is decided on the bytes. HEIC
  is recognised in order to be refused by name, because nothing here decodes
  it — the page re-encodes camera captures to JPEG before they ever leave the
  phone, which is what makes an iPhone capture work.

## The installed app

The web app manifest keeps its identity (`id`, `start_url` and `scope` all stay
`/scan`), so a phone that already installed the card page gets the new home
screen in place — not a second icon beside the old one. The name becomes
*Numériser*, and the manifest gains three shortcuts, one per tile.

The service worker is the one from `bf_contact_enrichment`, extended to cache
this module's assets too, under a new cache name.

### Finding it in the first place (18.0.1.1.0)

A page nobody knows about is a page nobody uses: before this version, `/scan`
had to be typed. The module now adds a **Numériser** entry to the application
grid, held by the internal-user group, so every employee has it where they
already look.

The entry is a URL action, never a server action: Odoo refuses to run a server
action for anyone who lacks write access on its model, so a tile built that way
fails for every ordinary employee. A test pins the action type for that reason.

## Requirements

`bf_contact_enrichment`, `bf_bloc_notes` and `account`. Reading is optional:
without `bf_invoice_ocr` the invoice tile still deposits the bill, and the page
says the automatic reading is not installed here.
