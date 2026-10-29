# Preferred Names and Pronouns: Quotation Signature Bridge (`bf_preferred_name_sign_sale`)

The **Signed By** field of a quotation carries the signer's frozen legal name.

When a quotation is signed through `bf_sign`, `bf_sign_sale` writes the names
of the signers in **Signed By**: it is the trace of the act on the quotation.
This bridge puts there the name on the act frozen at sending
(`bf_preferred_name_sign`), or the signer name when there is none.

## What it does

- The name on the act is written in **Signed By** in the same write as
  `bf_sign_sale`, before the order is confirmed. A quotation PDF sent to the
  customer at confirmation therefore carries the same name as the order.
- The replacement applies only to the signed order, not to other records the
  confirmation creates.

## Requirements

- Odoo 18.0
- `bf_preferred_name_sign` (Symbifox)
- `bf_sign_sale` (Symbifox)

Installs itself when both are present.

## Changelog

- **18.0.1.0.0**: First release.

## Licence

Business Source License 1.1 (`LICENSE`). Each version converts to
LGPL-3.0-or-later on its Change Date: 2030-10-29 for this version.
