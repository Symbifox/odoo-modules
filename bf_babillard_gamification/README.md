# Babillard : reconnaissance (`bf_babillard_gamification`)

A bridge between the noticeboard (`bf_babillard`) and Odoo's badges (`hr_gamification`). It installs itself as soon as both modules are present.

In Odoo, a badge given by a colleague is almost invisible: one email goes to the recipient, and the badge sits in a tab of their employee form. This bridge posts a card on the noticeboard instead, with the giver's words if there are any.

Badges awarded automatically by a challenge or a goal are not posted, because that is not recognition between colleagues. There are no points and no ranking: each badge makes one card, and nothing adds up. The card belongs to the recipient's company.

## Licence

Business Source License 1.1; see `LICENSE`. Each version converts to LGPL-3.0-or-later four years after its release.
