# Symbifox Appointment (`bf_appointment`)

Public self-service booking pages, extending *Resource Booking* (OCA).

## Features

- **Public booking pages** per booking type, with slots computed from resource
  availability.
- **Client portal**: self-service confirmation, rescheduling and cancellation.
- **Privacy consent** built into the booking flow (`privacy_consent`).
- **Automatic task/project creation** at booking time.
- **Symbifox branded emails** through `bluefox_branding` (header, brand
  colours, per-company footer).
- **Onboarding wizard** (`bf_onboarding_base`) to configure booking types.

## Dependencies

`resource_booking`, `portal`, `mail`, `project`, `privacy_consent`,
`bluefox_branding`, `bf_onboarding_base`, `bf_timezone`.

## Licence

Distributed under the **LGPL-3** licence. See the `LICENSE` file.
