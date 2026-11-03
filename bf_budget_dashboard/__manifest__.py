{
    "name": "Budgets opérationnels — tuile tableau de bord",
    "summary": "Consommation du budget ouvert et lignes signalées, sur l'accueil",
    "version": "18.0.1.0.0",
    "category": "Accounting/Accounting",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Pont Budgets ↔ Tableau de bord
==============================

S'auto-installe quand `bf_budget` et `bf_home` (qui porte l'ancien
`bf_dashboard`) sont tous deux installés. Ajoute une tuile qui dit trois
choses :

* la part du budget ouvert déjà engagée, en pourcentage ;
* le nombre de lignes signalées, en rouge, parce que c'est le seul chiffre de
  la tuile sur lequel on agit tout de suite ;
* l'écart au théorique, c'est-à-dire si on dépense plus vite que le plan à ce
  jour.

Un seul budget est cité, le plus récent qui soit ouvert — celui dont la
personne répond s'il y en a un. Un pourcentage qui mélangerait des exercices
ou des sens (dépense, revenu) ne voudrait rien dire. Le compte de lignes
signalées, lui, porte sur tous les budgets ouverts de la société.

La tuile est pour qui lit les budgets (`bf_budget.group_bf_budget_user`). Pour
les autres, elle ne s'affiche pas, et rien ne s'écrit au journal.
""",
    "depends": [
        "bf_budget",
        "bf_home",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_budget_dashboard/static/src/xml/tile.xml",
        ],
    },
}
