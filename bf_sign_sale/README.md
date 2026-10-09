# Symbifox — Signature for Sales (`bf_sign_sale`)

A bridge module wiring sales orders into `bf_sign` electronic signature.

## What it does

- Adds a "Send for signature" action on `sale.order` (through the
  `bf.sign.mixin` mixin).
- Renders the quotation / sales order as a PDF
  (`sale.action_report_saleorder`), creates a linked `bf_sign` signature
  request, then posts the signed document back into the order's thread once
  everyone has signed.

## Dependencies

`bf_sign`, `sale`.

## Languages (v18.0.2.2.0)

Labels and messages are written in English in the source; the French ships in `i18n/fr_CA.po`. Before this version they read in French for every user, including users set to English. A refusal sent from the signer's public page is noted on the order in the salesperson's language, not in the language of the signer's browser.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2029-07-20, this version converts automatically to
  **LGPL-3.0-or-later**.
