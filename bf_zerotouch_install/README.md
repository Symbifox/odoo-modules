# Symbifox — Blue Fox OS Zero-Touch Install (`bf_zerotouch_install`)

Install a **Blue Fox OS** workstation by typing one word: your organisation's
domain. This module serves the kickstart that the Blue Fox OS installer fetches
from its boot menu, rendered from the policy your Odoo already holds
([`bf_policy`](../bf_policy)). The person at the keyboard approves the install
on their phone; everything else — disk, language, keyboard, sign-in, apps —
comes from Odoo.

## How it works

1. At the installer's boot menu, someone picks *Install Blue Fox OS
   (zero-touch)* and types the organisation's domain.
2. The installer downloads `https://<domain>/blue-fox-install.ks` from this
   module. The kickstart is rendered from the company's policy record: OS
   image, locale, keyboard, timezone and identity-provider endpoints.
3. Its `%pre` step runs an **OIDC device flow**: a code and a QR code appear on
   screen, and the operator approves on a second device, so multi-factor
   authentication is preserved and no password is typed on the machine.
4. With that approval, the installer fetches the person's merged policy, enrols
   the machine (and, if enabled, escrows its disk passphrase) through
   `bf_policy`, then installs the image.
5. Its `%post` step applies the policy — hostname, locale, directory sign-in,
   root account, Flatpak apps — and leaves the session settings for the
   first-boot welcome agent.

The install never aborts because the network or the identity provider failed:
it falls back to the organisation's defaults and asks at the console for what
it could not decide. In particular, when the installer cannot confirm what is
already on the disk, it **does not partition by itself** — it asks, so a
machine that already carries another system is never wiped by accident.

## What is served

- `GET /blue-fox-install.ks` — public by design: the installer fetches it
  before any login can exist. It contains no secret, only the image reference,
  the policy URL and the public OIDC endpoints.
- The response embeds two scripts from `data/`: `bfos_provision.py` (the `%pre`
  device flow, enrolment, disk plan and escrow) and `bfos_apply.py` (the
  `%post` policy applier). Both are standard-library Python, since the
  installer environment has no extra packages.
- A company with no OCI image configured on its policy record has zero-touch
  disabled (HTTP 404). With several companies in one database, a request is
  routed by the host it arrives on, and an unknown host is refused.

## Requirements

- Odoo 18.0 and [`bf_policy`](../bf_policy).
- A Blue Fox OS installer image whose boot menu offers the zero-touch entry.
  The OS image and installer are maintained separately and are not part of this
  repository; the files under `data/` mirror the installer scripts they ship.
- An OIDC provider supporting the device authorisation grant (built and tested
  against Authentik).

## Configuration

On the company's **Policy › Org Defaults** record, fill the *Zero-Touch* tab:
OCI image, secondary locale, installer keyboard layouts, device-authorisation
and token endpoints, and the public OIDC client id. The domain on that record
is the one people type at the boot menu.

## Licence

Business Source License 1.1 — see [LICENSE](LICENSE). Each version converts to
LGPL-3.0-or-later on its Change Date (2030-09-26 for this version), or on the
fourth anniversary of its first public distribution, whichever comes first.
