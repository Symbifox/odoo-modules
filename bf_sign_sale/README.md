# Blue Fox — Signature for Sales (`bf_sign_sale`)

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

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2029-07-20, this version converts automatically to
  **LGPL-3.0-or-later**.
