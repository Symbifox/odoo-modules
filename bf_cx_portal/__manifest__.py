{
    "name": "Expérience client : feedback au portail",
    "summary": "Le client consulte ses feedbacks et commente depuis son portail",
    "version": "18.0.1.0.0",
    "category": "Marketing/Customer Experience",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Pont Expérience client / Portail
================================

S'auto-installe quand bf_cx et portal sont tous deux installés.
Ajoute au portail client une page « Mon feedback » (/my/feedback) où le
client connecté consulte les feedbacks rattachés à sa société (le
feedback interne 360 est exclu) : date, type, note et commentaire. Il
peut aussi y soumettre un commentaire libre, enregistré comme verbatim
(canal « Autre ») dans le registre unifié de bf_cx.

Aucun envoi sortant : ce pont n'envoie ni courriel ni sondage, il ne
touche donc pas aux garde-fous de sollicitation.

Étanchéité
----------
Le contrôleur (auth utilisateur) filtre toujours sur le partenaire
commercial de l'utilisateur connecté, et les gabarits ne reçoivent que
des dictionnaires en liste blanche, jamais l'enregistrement. La création
du commentaire passe par sudo mais chaque champ est forcé côté serveur :
seule la valeur du commentaire vient de l'utilisateur. Une ACL en
lecture seule et une règle d'enregistrement bornent aussi l'accès RPC du
groupe portail à ses propres feedbacks non internes.
""",
    "depends": [
        "bf_onboarding_base",
        "bf_cx",
        "portal",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_cx_portal_security.xml",
        "views/portal_templates.xml",
    ],
}
