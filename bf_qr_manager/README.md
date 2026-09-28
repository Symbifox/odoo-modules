# Managed QR codes (`bf_qr_manager`)

Print 500 QR labels before you know where each one will go. Every code leads to
an address the system controls: it is linked on first scan, re-linked without
reprinting, and reset when the object leaves.

A QR label is a tag without a chip. The module extends **Symbifox Pastilles**
(`bf_nfc`) and inherits its register, gestures (open a record, open an address,
log a visit, menu), tap log and safeguards.

## What it adds

| Feature | How |
|---|---|
| Blank label batches | Numbered per prefix: a second `QR` batch continues after the last number, never restarts at 1. |
| First-scan linking | The Pastilles managers, plus the groups each company picks in *Configuration → Settings*. Anyone else sees a neutral page. |
| Reset | The label becomes blank again. Its code, number and tap log are kept. One confirmation per label on the form; one confirmation for the whole selection from the list. |
| Linking by spreadsheet | `.xlsx` or `.csv`: numbers, ranges (`12-20`), references (`QR-0012`) or codes. Every line is checked (dry run of the real linking) before anything is written. A pre-filled sheet can be downloaded from the batch. |
| Label sheets | Ten common Letter and A4 formats, drawn to the millimetre with ReportLab, with a start offset to finish a partly used sheet. Custom formats can be added. |
| Branded QR | Logo in the centre at error correction level H; contrast of at least 4:1 and a code darker than its background are enforced. |
| Public door `/q/<code>` | A label marked *Opens without an account* and pointing to an address, a published link page or a published event opens without an account. Anyone allowed to link can therefore point a label at any http(s) address: it redirects from your own domain. |
| Mobile app | The Symbifox Pastilles app does not list blank labels under *My tags* (capped at 100 lines, they would push real tags out of sight) and never offers the blank gesture for engraving a chip. |
| Languages | French source, English (en_CA) catalogue. |

## Design rules

- **The public door runs no gesture.** It only follows an address computed from
  the label, and refuses any gesture that writes. An anonymous scan only adds a
  log line and bumps the label's scan counter; link previews count as scans.
- **Unknown or retired code: plain 404**, like the core.
- **Companies are walled off.** Linking, resetting and printing check the label's
  company before the role: a manager of one company cannot touch another's labels.
- **Linking is checked in one place** (`bf.nfc.tag._associer`), for the form and
  the spreadsheet alike. Company groups have no write access on tags: the method
  checks first, then writes only the linking fields.
- **The target record is not tracked in the chatter history.** The core (`bf_nfc`)
  tracks `res_id`, which Odoo cannot track for a `Many2oneReference`: every write
  of the target failed at commit time with "Unsupported tracking on field res_id",
  which unit tests and rolled-back rehearsals never reach. This module turns that
  tracking off; each link and reset leaves a note naming the record instead.
- **The logo is refused under 25 mm** (quiet zone included); under 20 mm the
  sheet prints with a warning.

## Tests

`tests/`: 83 tests covering batches, linking and permissions, company walls, the public
door, printing, the spreadsheet import, settings, label formats, the mobile app and the
translation catalogue.

## Label formats

Dimensions come from the gLabels template database (MIT licence: "no copyright is
claimed on the facts contained within the database"), converted to millimetres.
No Avery template file is copied. Trade references such as "Avery 5160" are
quoted in plain text to say which sheets fit; no affiliation is implied.

## Licence

Business Source License 1.1, see `LICENSE`.
