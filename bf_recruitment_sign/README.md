# Recruitment: the offer goes out for signature (`bf_recruitment_sign`)

`auto_install` bridge between `bf_recruitment_letter` and `bf_sign`. It grafts
`bf.sign.mixin` onto `letter.document`.

⚠️ **Every** letter becomes signable, not only the offer. That is what signing
an offer requires, since the offer IS a letter, and later candidate-facing
letters inherit it.

## 🔴 The branded stationery under the signature, not the bare body

`letter.document` renders its PDF in **two steps**: the QWeb report produces the
body, then `_get_pdf_binary()` overlays it onto the company's uploaded letterhead
when the mode is `pdf_overlay`. A bridge that merely declared the report would
therefore send for signature a document **without the letterhead** — not the one
the candidate read.

The module returns the complete PDF through `_sign_document_file()`, keeping
`_sign_report_ref()` as a fallback. A test proves it **by the pair**: with a
letterhead uploaded, the bridge's output and the bare report's output **differ**.
If they were identical, `_sign_document_file()` would be doing nothing.

## ⚠️ The hook is version-dependent

`_sign_document_file()` exists on `bf.sign.mixin` from **`bf_sign` 18.0.3.22.0**
onward. On an older lineage the override is **silently ignored** and it is the
report that gets signed, which is correct in four letterhead modes out of five.

For the fifth, `pdf_overlay`, the module **raises** rather than sending for
signature a document that is not the one that was shown, and the message names
both ways out: upgrade `bf_sign`, or choose a generated letterhead.

🔴 **The probe interrogates the MIXIN, not itself.** The bridge overrides
`_sign_document_file`, so `hasattr(self, …)` would **always** answer yes,
including on a lineage that never calls it: exactly the shape of a bridge you
believe is active and that is inert. A seam (`_sign_installed_mixin`) makes that
choice testable — without it, a mutation replacing the probe with the naive read
passes without a single test failing. That is measured, not assumed: it did pass,
before the seam existed.

## What it refuses to do

* A letter in **draft** does not go out for signature. You sign a settled text.
* It does **not** advance the application. A signed offer is not a start date,
  and `date_closed` is the **hire** date: setting it automatically would count
  the person among the job's hires and distort the cost per hire in
  `bf_recruitment_expense`. The signature is logged on the application's thread;
  the decision stays human.

## Test evidence

A suite including the letterhead pair and the guard pair: `pdf_overlay` raises on
an old lineage, `banner` passes. **Four mutations** placed and removed, each one
bringing down its test.

⚠️ **Test-harness trap worth knowing**: in test mode `_render_qweb_pdf` returns
**HTML**, not a PDF, for want of workers to call wkhtmltopdf. A test expecting
`%PDF-` then fails for a reason that has nothing to do with the module. The
`force_report_rendering=True` context fixes it.
