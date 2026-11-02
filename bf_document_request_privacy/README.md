# Document Requests: Privacy Bridge (`bf_document_request_privacy`)

[Document Requests](../bf_document_request) (`bf_document_request`) collects
documents a client photographs from their phone: identity documents, proofs of
address, of insurance or of income. Direct, often sensitive personal
information. The module carries its own retention (`bf.document.expiry.policy`,
with its `retention_days`), while the retention schedule and the destruction
register live in [Privacy Consent](../privacy_consent) (`privacy_consent`).
This bridge connects the two.

- **Version**: `18.0.1.1.1`, for Odoo 18 Community.
- **Licence**: **BUSL-1.1**: production use allowed for your own internal
  business operations; providing the module as a product or service to third
  parties (hosted, managed or resold) requires a written agreement. Converts to
  **LGPL-3.0-or-later** on the Change Date stated in [`LICENSE`](LICENSE).
- **Languages**: English source, French (Canada) catalogue included. The
  register texts this bridge delivers (purpose summary, retention rule and
  policy) are stored in one language by `privacy_consent`: on a
  French-speaking company they are written in French at install and on
  upgrade, unless someone has already adapted them.
- **Installation**: automatic when `bf_document_request` and
  [Secure Transfer: Privacy Bridge](../bf_securetransfer_privacy) are
  installed.

## What it does

1. **It attaches each expiry policy to a retention schedule rule.** The period
   a policy applies becomes the application of a documented rule (legal basis,
   final disposition, destruction method) instead of an orphan number. The
   policy form shows whether the two periods agree.
2. **It has each document type declare the category of personal information
   it carries.** Without it, the register entry for an identity document would
   say "categories unknown".
3. **It enriches the register entry** written by the base bridge when the
   purged transfer comes from a request: the request and the client are named,
   the documents are listed, the retention rule is quoted, and the number of
   people concerned is corrected (the client, not the employees who received
   the files).
4. It declares the purpose and its retention policy in the register of
   processing activities, and makes requests and their lines classifiable.

## Design choices

- **The schedule, not `privacy.retention.policy`.** A retention policy is
  unique per purpose, and all document requests share one purpose: several
  expiry policies cannot each attach to it. A schedule rule is per document
  type, the same granularity.
- **It destroys nothing.** Destruction goes through `secure.transfer._purge_s3()`
  only, called when a request expires or is cancelled, and by the base's purge
  job for active transfers.
- **One entry per purge.** The enrichment goes through
  `_privacy_destruction_vals()`, the base bridge's extension point, not through
  a second override of the purge: two bridges writing their own entry would
  leave two unalterable entries for one destruction.
- **The declared periods drive nothing.** The schedule documents the regime;
  the policy's `retention_days` sets the date. When the two contradict each
  other, the policy form says so and corrects nothing: deciding which one is
  right belongs to the person responsible for the processing.
- **The delivered texts are a starting point.** The purpose, its summary and
  the schedule rule are `noupdate`: the organisation adapts them, and updates
  never overwrite them.
