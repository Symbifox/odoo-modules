# Receipt reading (`bf_expense_ocr`)

Photograph the receipt; the expense fills itself in — merchant, date, total,
taxes and tip.

## What it is built on

**`bf_ai_bridge`** — the local `claude-chatbot-bridge` service and, behind it,
`claude -p` running on the **tenant's own Claude subscription**. The bridge
picks the credentials directory from the tenant a system declares, so a system
announcing `bsi` is read on BSI's subscription, and a tenant whose session is
not open fails loudly rather than being billed to someone else's.

⚠️ **It is deliberately not `bf_llm`.** That gateway only speaks HTTP APIs with
a key (`anthropic`, `openai`, `openai_compatible`), which is exactly why its
provider record ships disabled and keyless: nobody pays per token when the
subscription is already there. An extraction module built on it cannot work in
this house, however green its test suite looks — 1.0.0 of this module made that
mistake and could never have run.

The extraction schema lives on the bridge side, at `/ocr/receipt`, with its
prompt-injection guardrails. This module contributes the arithmetic guard,
which is what decides whether anything gets written at all.

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

An undeclared tenant raises `UserError`, a missing socket surfaces as
`FileNotFoundError` from the transport, and a failed read comes back inside the
bridge's envelope. None of it must cost the user their photo, so the automatic
path swallows all three and records them on the record.

## What it deliberately does not guess

The expense category and the vendor. A miscategorised meal is one click to fix;
a meal miscategorised *automatically* is fixed whenever someone notices.

## What it does not do

* **Calendar correlation.** Once the date and time are read, one could find the
  overlapping event and propose its analytic distribution. That is a second
  storey, not a condition of the first.
* **Anonymise.** The receipt goes as-is to the bridge, which reads it on the
  tenant's subscription. Choosing the subscription *is* the privacy decision.

## Tests

34 tests, `--test-tags '/bf_expense_ocr'`. None of them calls the real bridge:
`bf.ai.bridge.call` is replaced by a spy that returns the envelope under test,
so what is exercised is the guard and the mapping, not `claude -p` — and a test
pass does not consume anyone's subscription.

The spy also asserts on what would have been *sent*. That is how the
"off by default" tests prove nothing left the instance, and how one test pins
the `org` field: a wrong tenant does not make the call fail, it makes it
succeed **on somebody else's subscription**, so the check has to be on the way
out, not on the way back.
