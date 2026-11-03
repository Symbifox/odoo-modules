{
    "name": "Banque d'heures — section du digest quotidien",
    "version": "18.0.1.0.4",
    "category": "Services/Project",
    "summary": "Ajoute une section « Paliers franchis » au digest quotidien",
    "description": """
Pont Banque d'heures ↔ Digest quotidien
=======================================

S'auto-installe quand bf_hour_bank et daily_todo_digest sont tous deux
installés. Injecte une section « Banques d'heures » dans le courriel du
digest quotidien :

* les paliers franchis sur l'horizon retenu (hour.bank.threshold.event) ;
* le rappel des banques actives qu'aucun palier ne surveille.

Le second point est le trou que le module existe pour montrer : le champ
threshold_mode de hour.bank.client vaut « disabled » par défaut, si bien qu'une
banque peut vivre sans qu'aucun palier ne la surveille. Aucun avis ne partira
alors, quoi que vous ayez promis au client.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": [
        "bf_hour_bank",
        "daily_todo_digest",
    ],
    "data": [
        "views/daily_digest_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": True,
}
