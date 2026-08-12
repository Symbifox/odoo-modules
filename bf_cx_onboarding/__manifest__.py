{
    "name": "Expérience client : panneau de mise en route",
    "summary": "Panneau de mise en route du module Expérience client",
    "version": "18.0.1.0.0",
    "category": "Marketing/Customer Experience",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Pont Expérience client : mise en route
======================================

S'auto-installe quand bf_cx et bf_onboarding_base sont tous deux
installés. Ajoute un panneau de mise en route en quatre étapes :
créer un programme de mesure, configurer la cadence
anti-sursollicitation, lancer une première vague d'envoi et
configurer l'équipe Plaintes.

Chaque étape se coche d'elle-même quand l'action correspondante est
posée dans le module (création d'un programme, envoi d'une vague,
ajustement de la cadence, première plainte consignée). Le panneau est
purement interne : aucun envoi au client.
""",
    "depends": [
        "bf_cx",
        "bf_onboarding_base",
    ],
    "data": [
        "data/bf_onboarding.xml",
    ],
}
