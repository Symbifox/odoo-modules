# Client onboarding progression (`bf_client_onboarding`)

Adds a **Progression** tab to `project.project` that gathers, in one view, how far
a client has moved through onboarding.

## Why

Onboarding a client is half a dozen small facts scattered across as many models:
is the NDA signed, has the intake questionnaire come back, did the kickoff
happen, is there a knowledge matrix yet, are the consents recorded. Each lives
somewhere sensible on its own, and nowhere together — so the answer to "where is
this client at?" is a tour of six menus.

This module does not create new state. It reads what the other modules already
know and shows it in one place, with a single status field on top.

## What it provides

- A status bar: `not_started → nda_pending → nda_signed → intake_pending →
  intake_completed → kickoff_done → active`, plus `on_hold` with a reason.
- NDA generation through `bf_letter_writer`, with optional signature tracking.
- Intake through a `survey.survey`, auto-advancing when the response arrives.
- Kickoff linked to a `meeting.record`, confirmed by hand.
- Aggregated signals: knowledge matrix, recorded consents, last meeting, hour
  bank.

## Requirements

Odoo 18 Community, `project`, `survey`, and these modules from this repository:
`bf_meeting`, `bf_hour_bank`, `bf_letter_writer`, `privacy_consent`,
`project_knowledge_matrix`.

## License

LGPL-3.
