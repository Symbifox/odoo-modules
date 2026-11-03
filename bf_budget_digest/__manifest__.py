{
    "name": "Budgets opérationnels — digest quotidien",
    "summary": "Section « Budget » : les lignes signalées des budgets dont on répond",
    "version": "18.0.1.0.0",
    "category": "Accounting/Accounting",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Pont Budgets ↔ Digest quotidien
===============================

S'auto-installe quand `bf_budget` et `daily_todo_digest` sont tous deux
installés. Injecte une section « Budget » dans le digest quotidien : les lignes
signalées des budgets ouverts dont la personne est responsable, avec le prévu,
l'engagé, le théorique et l'écart.

La section suit le responsable, exactement comme l'alerte du module : celle-ci
lève une activité UNE FOIS, au franchissement du seuil ; le digest, lui, le
rappelle chaque matin tant que la ligne reste signalée. L'activité est
l'événement, le digest est le rappel.

La section n'apparaît que s'il y a une ligne signalée. Les journées calmes
restent calmes.
""",
    "depends": [
        "bf_budget",
        "daily_todo_digest",
    ],
    "data": [
        "views/daily_digest_views.xml",
    ],
}
