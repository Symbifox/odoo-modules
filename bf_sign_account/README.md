# Blue Fox — Signature for Accounting (`bf_sign_account`)

A bridge module adding **"Send for signature"** on accounting documents
(`account.move`: customer invoices and vendor bills) through the
`bf.sign.mixin` mixin from [`bf_sign`](../bf_sign).

The invoice is rendered as a PDF (the standard `account.account_invoices`
report), a linked `bf_sign` signature request is created, then the signed
document is posted back into the document's thread once every signer has
signed.

The header button is hidden on pure journal entries (`move_type == 'entry'`);
it only appears on invoices and credit notes.

## Dependencies

`bf_sign`, `account`.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2029-07-20, this version converts automatically to
  **LGPL-3.0-or-later**.
