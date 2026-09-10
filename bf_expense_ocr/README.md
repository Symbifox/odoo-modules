# Receipt reading (`bf_expense_ocr`)

Photograph the receipt; the expense fills itself in — merchant, date, total,
taxes and tip.

## What it is built on

Not on `bf_invoice_ocr`. Four fifths of that module match a vendor and build
invoice lines, neither of which a restaurant receipt asks for. The shared base
is **`bf_llm`**: the gateway, its normalised envelope and its Tesseract
fallback. This module contributes an extraction schema and an arithmetic guard.

What *is* reused from `bf_invoice_ocr` is the vocabulary — `ocr_state`,
`ocr_scanned_date`, `ocr_confidence`, `ocr_raw_response`, `ocr_error_message`.
Two OCR surfaces in one database should read the same way.

## The control that decides everything

A thermal receipt, creased, photographed at an angle, reads badly. The guard is
therefore not the model's self-reported confidence — that is an opinion — but
the receipt's own arithmetic:

    subtotal + GST + QST + other taxes + tip == total

Within two cents, because a receipt rounds its GST and QST separately too.

When it balances, the amounts are written to the expense. When it does not,
**nothing is written**. We do not know which of the five numbers is wrong, so
pre-filling any one of them would present a guess as a fact. The expense goes to
`doubt`, the raw extraction stays readable, the reason is shown above the form,
and the person types the amounts — which they would have done anyway.

## The tip when it is not printed

Many receipts print the amount paid without breaking out the tip. It is then
derived: `total − (subtotal + taxes)`. The residual is accepted only if it is
positive and plausible — under 40% of the subtotal. Beyond that, the reading
went wrong somewhere else, and the field stays empty.

## Nothing leaves without a decision

A meal receipt names a merchant, a date and a time. Cross-referenced with a
calendar it says who the person ate with. Sending it to a language model is a
decision, not a side effect:

| | Default |
|---|---|
| The **button** on the expense | always available — the person who took the photo chooses |
| **Read on upload** | **off** — one checkbox in Expenses settings |
| **Periodic catch-up cron** | **off** — `active=False` in the data file |

The cron, once on, only looks at draft expenses that carry a readable
attachment and have never been read.

## What it hooks into

Both upload doors: `create_expense_from_attachments` (the phone gesture — a
photo *creates* the expense) and `attach_document` (a photo added to an existing
one). Images as well as PDFs, since a phone is what feeds this.

An unconfigured gateway raises `UserError`; a model failure comes back inside
the envelope. Neither must cost the user their photo, so the automatic path
swallows both and records them on the record.

## What it deliberately does not guess

The expense category and the vendor. A miscategorised meal is one click to fix;
a meal miscategorised *automatically* is fixed whenever someone notices.

## What it does not do

* **Calendar correlation.** Once the date and time are read, one could find the
  overlapping event and propose its analytic distribution. That is a second
  storey, not a condition of the first.
* **Anonymise.** The receipt goes as-is to whichever provider `bf_llm` is
  configured with. Choosing the provider *is* the privacy decision.

## Tests

32 tests, `--test-tags '/bf_expense_ocr'`. None of them calls the real gateway:
`for_feature` is replaced by a spy that returns the envelope under test, so what
is exercised is the guard and the mapping, not the language model. The spy also
asserts on what would have been *sent* — that is how the "off by default" tests
prove nothing left the instance.
