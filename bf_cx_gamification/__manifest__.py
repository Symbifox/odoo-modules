{
    "name": "Expérience client : XP Fox Quest",
    "summary": "XP Fox Quest pour la boucle fermée et les plaintes résolues",
    "version": "18.0.1.0.0",
    "category": "Marketing/Customer Experience",
    "author": "Blue Fox Inc.",
    "website": "https://bluefoxconsultant.com",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Pont Expérience client ↔ Fox Quest
==================================

S'auto-installe quand bf_cx et bf_gamification sont tous deux installés.
Récompense la discipline CX dans Fox Quest : de l'XP est accordé au
responsable quand un suivi de boucle fermée est complété (feedback passé
en « Traité ») et quand une plainte client est résolue. Une seule
attribution par enregistrement (drapeau anti-double), et toute erreur du
calcul d'XP est journalisée sans jamais bloquer le flux d'origine.
Aucun envoi au client.
""",
    "depends": [
        "bf_cx",
        "bf_gamification",
    ],
    "data": [
        "data/gamification_xp_rule_data.xml",
    ],
}
