# Symbifox — Signing Law 25 consents (`bf_sign_privacy`)

A bridge module wiring the native [`bf_sign`](../bf_sign) electronic signature
engine into the [`privacy_consent`](../privacy_consent) consent module.

`privacy_consent` can send its consents out for signature through **external**
platforms (DocuSeal, LibreSign). This bridge adds a third, **internal** route:
"Send for signature" on the consent (`privacy.consent`) through the
`bf.sign.mixin` mixin.

The **consent notice** is rendered as a PDF by this module
(`bf_sign_privacy.action_report_consent_form`) — the subject, the purpose and its
plain-language summary, the notice text with its version and effective date, and
the date the consent expires. A linked `bf_sign` signature request is created,
and the signed document is posted back into the consent's thread once signed.
The consent subject (`subject_partner_id`) is prefilled as the default signer,
since the mixin's own fallback looks for a `partner_id` that `privacy.consent`
does not have.

The notice is deliberately *not*
`privacy_consent.action_report_consent_certificate`: that report is bound to the
`privacy.consent.evidence` model, not to `privacy.consent`, so handing it a
consent id makes the render raise `MissingError`. On the substance, a
certificate attests to something already done, which is not what a person is
asked to sign.

Consent artefacts can therefore be signed with the in-house simple electronic
signature (SES) engine, which holds up under Quebec law (see
[`bf_sign`](../bf_sign)), **without depending on an external signing service**.

## Installation

Auto-installs when `bf_sign` **and** `privacy_consent` are both present
(`auto_install: True`).

## Dependencies

`bf_sign`, `privacy_consent`.

## Licence

Distributed under the **Business Source License 1.1** (BUSL-1.1). See the
[`LICENSE`](LICENSE) file for the exact parameters.

- **Allowed without an agreement**: production use for your own internal
  business operations.
- **Requires a written agreement**: providing the module as a product or
  service to third parties, whether hosted, managed or resold.
- **Change Date**: on 2029-07-20, this version converts automatically to
  **LGPL-3.0-or-later**.
