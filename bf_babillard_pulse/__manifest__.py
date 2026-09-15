# -*- coding: utf-8 -*-
{
    "name": "Babillard : pulse",
    "summary": "Les scores d'une vague fermée, quand ils franchissent leurs seuils",
    "version": "18.0.1.0.3",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_babillard", "bf_employee_experience_pulse"],
    "auto_install": True,
    "installable": True,
    "description": """
Pont Babillard ↔ Pulse
======================

S'auto-installe quand `bf_babillard` et le pulse sont tous deux là. À la
fermeture d'une vague, une carte paraît au babillard avec les axes dont le score
est affichable.

**Les seuils du pulse restent la loi.** Le pont ne lit que
`is_displayable` et n'affiche que `display_score` : un axe sous le seuil de
répondants n'apparaît pas, et aucun segment d'équipe n'est nommé. Répondre à un
sondage anonyme ne doit jamais devenir lisible par soustraction au babillard.
""",
}
