# Babillard : sondages Odoo (`bf_babillard_survey`)

A bridge between the noticeboard (`bf_babillard`) and Surveys (`survey`). It installs itself as soon as both modules are present.

The noticeboard carries polls of its own: one question, a few choices, one click. This bridge serves the other need, the questionnaire. An Odoo survey has pages, sections and scored questions, and it is answered elsewhere. The feed announces it and gives its link; it does not replay it.

An editor announces a survey from its form, with the **Announce on the noticeboard** button. It is a deliberate gesture, not an automatic one: an open questionnaire is not necessarily company news, and the feed is the one place everybody reads.

The card is posted once. Announcing the same survey again opens the existing card instead of posting a second one.

## What it refuses, and why

A survey restricted to invited people is not announced. Its start link only works with the personal invitation that carries the answer token, so a card read by the whole company would lead nowhere. The bridge says so and asks for the survey to be opened to anyone with the link first.

Publishing to the noticeboard is reserved to the noticeboard's editorial group. The check lives in the method, not in the button: a `groups` attribute only guards a screen.

## Dependencies

`bf_babillard`, `survey`.

## Licence

Business Source License 1.1; see `LICENSE`. Each version converts to LGPL-3.0-or-later four years after its release.
