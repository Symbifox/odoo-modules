# Letter Writer (`bf_letter_writer`)

Writing branded official letters in Odoo 18 — locked letterhead, merge fields,
reusable templates and text blocks.

## Overview

`bf_letter_writer` adds a **Letters** application to Odoo for producing official
letters whose letterhead (logo, colours, signatory, footer) is **driven by the
company** and **locked**: the author only controls the body text, which
guarantees the brand rules are respected. The module is **multi-company /
white-label** — each company issues its letters with its own visual identity.

## Features

### Branded letterhead (white-label) — 5 modes
Driven by `res.company`; each company issues its letters with its own identity.
The chrome is locked — the user only edits the body, the salutation and the
closing.

- **Dark banner** *(generated)* — dark header with logo, brand colours
  (`report_brand_primary` / `report_brand_dark` through `bf_lexend`), address,
  letterhead note and footer.
- **Understated classic** *(generated)* — light variant with a rule.
- **Uploaded image (PNG/JPG)** — the company's letterhead image is used as a
  full-page background; the body is offset to clear it.
- **Uploaded PDF (overlay)** — the body is *stamped* onto each page of the
  company's letterhead PDF (through PyPDF2). Vector quality.
- **No letterhead (pre-printed paper)** — no chrome; the body is offset by a
  configurable margin so it prints cleanly on already-printed letterhead paper.

The *image*, *PDF* and *pre-printed* modes use the company's configurable
`letter_body_top_margin` / `letter_body_bottom_margin` margins.

### Letter templates
- Reusable templates (`letter.template`) with subject and body.
- Merge fields: `{{ object.field }}` tokens (Word mail-merge style) and QWeb
  `<t t-out="object.field"/>` syntax for advanced cases.
- A merge-field legend built into the template editor.
- Suggested text blocks attached to the template.

### Merge fields (mail merge)
- Applying a template to a letter renders the body with the recipient's values.
- **Batch merge**: one template, several recipients → one letter per recipient.
- An action on the contact list: "Create letters" from a selection.
- Detection of unfilled merge fields (non-blocking warning).

### Text blocks (quicktext)
- A library of reusable blocks (`letter.quicktext`), sorted by category, with a
  shortcut.
- Inserted into the body through a picker (at the start or at the end).
- Blocks can themselves contain merge fields.
- Three starter blocks are provided.

### Generation and sending
- Native branded PDF (QWeb), US Letter format.
- One-click PDF preview.
- Email delivery with the PDF attached, inside a branded email envelope.
- Native Odoo print button (report linked to the model).

### Lifecycle
- States: **Draft → Finalised → Sent**.
- Automatic numbering `LET-YYYY-NNN`.
- Recipient reference (name + address) frozen at finalisation.
- Chatter tracking and activities.
- **Archiving** of letters (`active` field plus an "Archived" filter).

### Convenience
- **Automatic title**: "Letter to [recipient]" as soon as a recipient is chosen
  (replaced by the template's subject when a template is applied).
- **Letterhead preview**: a thumbnail of the letterhead image on the company
  record and on the letter in image mode.
- Distinct **Preview PDF** (opens) and **Download PDF** buttons.
- Letters with unfilled merge fields are **highlighted** in the list.

### Optional integrations (detected at runtime)
- **`bf_persona`**: prefills the salutation and closing from the recipient's
  persona.
- **`bf_claude_chat`**: a "Review with Claude" button that opens the assistant
  with the letter in context.
- No hard dependency: the module works on its own, and the buttons disappear
  when those modules are not installed.

### Guided setup
- A 3-step onboarding panel: letterhead → templates → first letter.

## Models

| Model | Role |
|---|---|
| `letter.document` | The letter (instance) |
| `letter.template` | Reusable letter template |
| `letter.quicktext` | Reusable text block |
| `letter.merge.wizard` | Batch merge wizard |
| `letter.send.wizard` | Email sending wizard |
| `letter.quicktext.picker` | Block insertion wizard |

## Available merge fields

`{{ object.partner_id.name }}`, `{{ object.partner_id.parent_id.name }}`,
`{{ object.recipient_name }}`, `{{ object.letter_date }}`,
`{{ object.reference }}`, `{{ object.company_id.name }}`,
`{{ object.signatory_id.name }}`, `{{ object.signatory_function }}` — plus any
QWeb `<t t-out="..."/>` expression.

## Configuration

**Company** record → **Letters** tab:
- **Default letterhead** applied to new letters, plus top/bottom body margins
  (modes without a generated letterhead).
- **Letterhead image** (PNG/JPG) and **letterhead PDF**, both uploadable.
- Default **signatory**, function, signature image.
- **Letterhead note** and **footer** (generated modes).

Brand colours come from `bf_lexend`.

## Security

- Any internal user can write letters (create / edit).
- The **Letter writer / Manager** group manages templates, text blocks and the
  letterhead configuration.
- Multi-company rules on letters and text blocks.

## Dependencies

- Odoo: `base`, `mail`, `bf_lexend`, `bf_onboarding_base`.
- Python: `PyPDF2` (the "uploaded PDF" mode — shipped in the BF Odoo image).

## Technical notes

- The merge uses `mail.render.mixin` (the `mail.template` engine): an
  `inline_template` pass for `{{ }}` tokens, then a `qweb` pass if any
  `<t t-out>` remain. The result is wrapped in `Markup`.
- The PDF is a standalone QWeb template (not `web.external_layout`), like the
  other Symbifox branded reports; colours are injected from `doc.company_id`.
- `pdf_overlay` mode: `_get_pdf_binary()` renders the body without chrome, then
  `_stamp_on_letterhead()` overlays it on each page of the letterhead PDF
  through PyPDF2.
- `image` mode: full-page background (known limitation — it covers the first
  page; prefer `pdf_overlay` for multi-page letters).
- Optional modules are detected through `ir.module.module` (`installed` state).
- LGPL-3 licence.

## Tests

A `TransactionCase` suite in `tests/test_letter_writer.py` covering each
advertised feature. To run:

```bash
odoo -c <conf> -d <db> -u bf_letter_writer --test-enable --test-tags /bf_letter_writer --stop-after-init
```
