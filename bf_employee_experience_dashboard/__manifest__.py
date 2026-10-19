{
    "name": "Expérience employé — tuile tableau de bord",
    "summary": "Adhésion aux avantages et avantages payés que personne ne prend",
    "version": "18.0.1.0.2",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Pont Expérience employé ↔ Tableau de bord
=========================================

S'auto-installe quand `bf_employee_experience` et `bf_home` (qui porte
l'ancien `bf_dashboard`) sont tous deux installés. Ajoute une tuile qui dit
trois choses :

* le taux d'adhésion moyen du catalogue ;
* le nombre d'avantages payés que **personne** ne prend, en rouge, parce que
  c'est le seul chiffre du module sur lequel on agit tout de suite ;
* le coût annuel du catalogue.

La tuile est un indicateur de l'administration des avantages
(`hr.group_hr_user`), la seule qui voie tout le registre. Pour les autres, elle
ne s'affiche pas, et rien ne s'écrit au journal.
""",
    "depends": [
        "bf_employee_experience",
        "bf_home",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_employee_experience_dashboard/static/src/xml/tile.xml",
        ],
    },
}
