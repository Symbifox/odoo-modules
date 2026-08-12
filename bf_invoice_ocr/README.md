# bf_invoice_ocr — Invoice OCR Scanner

An Odoo 18 (CE) module that reads vendor bills in PDF form and prefills the
invoice fields automatically. Extraction goes through the **`bf_llm`** gateway,
which keeps the module provider-agnostic: Anthropic directly, OpenAI (with PDF
rasterisation), or a local OpenAI-compatible server.

## Features

- **A "Scan OCR" button** on every draft vendor bill
- **An hourly cron** for batch processing of new bills
- **Smart vendor matching** in 8 steps (VAT, name, email, phone, history)
- **Product matching** by code, vendor references and description
- **An OCR tab** showing status, confidence and the raw data

## Architecture

```
account.move (bf_invoice_ocr)
        │
        │ env["bf.llm"].for_feature("ocr").extract(pdf_bytes, OCR_PROMPT)
        ▼
bf.llm — provider-agnostic LLM gateway
        │
        ▼
Configured provider (Anthropic / OpenAI / local server)
        │  returns structured JSON
        ▼
Odoo applies the results (partner, ref, dates, lines, taxes)
```

## Configuration

1. Install `bf_llm` and configure at least one default LLM provider there:
   **Settings › Technical › LLM Providers**. The API key is encrypted at rest
   (Fernet) — see the `bf_llm` README.
2. Optional: set the default purchase tax applied to lines created by the OCR
   (see "Default tax" below).

## Fields added to `account.move`

| Field | Type | Description |
|-------|------|-------------|
| `ocr_state` | Selection | none / pending / done / error |
| `ocr_scanned_date` | Datetime | Date of the last scan |
| `ocr_confidence` | Float | Confidence score 0-100 |
| `ocr_raw_response` | Text | Raw JSON response from the model |
| `ocr_error_message` | Char | Error message where applicable |

## Data extracted by the OCR

- Vendor name, email, website, phone, tax number (GST/QST/NEQ/HST)
- Invoice number, dates (invoice and due), currency
- Line items (description, product code, quantity, unit price, amount)
- Subtotal, taxes, total
- Confidence score (0-100)

The prompt template (`OCR_PROMPT`) is supplied by `bf_llm` and keeps the
Canadian rules (GST/QST, NEQ) as well as the anti-injection guardrails: the
invoice content is treated as **untrusted data**, never as an instruction.

## Vendor matching (8 steps)

1. **Tax number (VAT/NEQ)** — searched in `vat` and `company_registry`
2. **Exact name** — `ilike` with `supplier_rank > 0`
3. **Email/web domain** — extracts the domain, searches `email` and `website`
   (ignores generic providers: gmail, hotmail, outlook, yahoo)
4. **Phone** — last 7 digits in `phone` / `mobile`
5. **Cleaned name** — strips legal suffixes (Inc, Ltd, Ltée, Corp, SENC, PBC…)
6. **Significant words** — accent normalisation, noise words excluded
   (Services, Solutions, Groupe, Canada…), matched on the first 2 distinctive
   words
7. **OCR history** — searches previously scanned bills with the same vendor name
8. **Fallback** — `is_company=True` with no `supplier_rank` filter

With no match, `partner_id` is left empty (manual entry).

## Product matching (3 steps)

1. **Product code** — `default_code` (ilike) or `barcode` (exact) from the
   `product_code` field extracted by the OCR
2. **Vendor references** — the matched vendor's `product.supplierinfo`, by
   `product_code` then `product_name`
3. **Description** — searched by distinctive words (5+ characters,
   `purchase_ok=True`), only when the match is unique (avoids false positives)

When a product is found, its vendor taxes (`supplier_taxes_id`) are used.
Otherwise the default purchase tax applies (see below).

## Default tax

The tax applied to lines created by the OCR (when the product does not impose
its own) is resolved in this order:

1. The `bf_invoice_ocr.default_tax_id` system parameter (an explicit
   `account.tax` id), when set;
2. The purchase tax configured on the company (`account_purchase_tax_id`);
3. Failing that, the company's first `purchase`-type tax.

If no tax is found, lines are created without one (to be completed manually). No
tax id is hardcoded in the module.

## Installation

```bash
# Install / update through your usual Odoo deployment procedure, e.g.:
odoo -d <database> -i bf_invoice_ocr --stop-after-init
```

Odoo dependencies: `account`, `bf_llm`.

## Licence

LGPL-3
