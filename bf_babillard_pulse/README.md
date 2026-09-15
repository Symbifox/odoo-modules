# Babillard : pulse (`bf_babillard_pulse`)

A bridge between the noticeboard (`bf_babillard`) and the employee pulse (`bf_employee_experience_pulse`). It installs itself as soon as both modules are present.

When a pulse wave closes, a card lists the axes whose score can be displayed.

The pulse's own thresholds still decide what can be shown. The bridge only reads `is_displayable` and only shows `display_score`. An axis below the respondent threshold does not appear, and no team segment is ever named: an anonymous survey must never become readable by subtraction on the noticeboard. The card stays in the company that ran the survey.

## Licence

Business Source License 1.1; see `LICENSE`. Each version converts to LGPL-3.0-or-later four years after its release.
