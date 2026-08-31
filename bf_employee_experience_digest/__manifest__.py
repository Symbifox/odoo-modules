{
    "name": "Expérience employé — digest quotidien",
    "summary": "Section « Avantages » : demandes en attente, usages sans droit, "
               "avantages que personne ne prend",
    "version": "18.0.1.0.0",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Pont Expérience employé ↔ Digest quotidien
==========================================

S'auto-installe quand `bf_employee_experience` et `daily_todo_digest` sont tous
deux installés. Injecte une section « Avantages » dans le digest quotidien :

* les demandes soumises qui attendent une décision ;
* les usages enregistrés sans droit ouvert, qui sont soit une erreur de saisie,
  soit une règle d'admissibilité à revoir ;
* les avantages payés que personne n'a pris depuis un an.

La section n'apparaît que s'il y a quelque chose à faire. Les journées calmes
restent calmes.
""",
    "depends": [
        "bf_employee_experience",
        "daily_todo_digest",
    ],
    "data": [
        "views/daily_digest_views.xml",
    ],
}
