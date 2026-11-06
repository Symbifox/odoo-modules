{
    "name": "Banque d'heures — section du digest quotidien",
    # 18.0.1.0.5 : le module s'importe de nouveau sur Python 3.10 et 3.11.
    #   Une apostrophe échappée dans l'expression d'une f-string n'est
    #   permise que depuis 3.12 (PEP 701) : ailleurs, le fichier ne se
    #   compilait pas, et le module tombait à l'import chez un hôte en
    #   Ubuntu 22.04. Rien ne change pour un hôte en 3.12.
    "version": "18.0.1.0.5",
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
