# Blue Fox — Signing corporate resolutions (`bf_sign_corporate`)

A bridge module wiring the native [`bf_sign`](../bf_sign) electronic signature
engine into the corporate resolutions of
[`project_knowledge_matrix`](../project_knowledge_matrix).

## What it does

- Adds a **"Send for signature"** action on the corporate resolution
  (`corporate.resolution`) through the `bf.sign.mixin` mixin, plus a
  "Signatures" smart button counting the linked requests.
- Renders the resolution as a branded PDF (the
  `project_knowledge_matrix.action_report_corporate_resolution` report),
  creates a linked `bf_sign` signature request, then posts the signed document
  (plus the completion certificate) back into the resolution's thread once
  everyone has signed.

## Default signers

The request is prefilled from the corporate register:

- **board resolution** → the active directors (`corporate.director`);
- **shareholders' resolution** → the mover (and the seconder, where
  applicable).

Signers remain editable on the draft request before sending. If a default
signer has no email address, the action says so clearly and names them, rather
than failing on a technical constraint.

## Dependencies

`bf_sign`, `project_knowledge_matrix`.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2029-07-20, this version converts automatically to
  **LGPL-3.0-or-later**.
