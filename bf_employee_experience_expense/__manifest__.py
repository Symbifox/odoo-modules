{
    "name": "Expérience employé — notes de frais",
    "summary": "Une dépense approuvée devient un usage d'avantage, au coût réel",
    "version": "18.0.1.0.0",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Pont Expérience employé ↔ Notes de frais
========================================

S'auto-installe quand `bf_employee_experience` et `hr_expense` sont tous deux
installés.

Sans ce pont, le coût d'un avantage vient du modèle : un montant de référence
multiplié par un nombre de personnes. C'est une estimation. Avec le pont, une
dépense approuvée porte le chiffre du terrain, et la ligne d'usage le reprend
tel quel.

Ce qu'il fait
-------------

* Un champ « Avantage » sur la note de frais et sur le produit de dépense.
  Le produit sert de valeur par défaut : on rattache le produit « Formation »
  à l'avantage une fois, et toutes les dépenses suivantes suivent.
* Au passage à l'état approuvé, une ligne d'usage **confirmée** est créée,
  d'origine « note de frais », avec le montant réellement engagé.
* Le lien est bidirectionnel : la ligne d'usage cite la dépense, la dépense
  cite la ligne. Une dépense ne peut pas produire deux usages.

Ce qu'il ne fait pas
--------------------

Il ne bloque rien. Une dépense rattachée à un avantage auquel la personne
n'avait pas droit ce jour-là passe quand même : la ligne d'usage porte alors
son drapeau « sans droit ouvert », comme n'importe quelle saisie manuelle.
Le signalement vaut mieux que le blocage, parce qu'il se voit.
""",
    "depends": [
        "bf_employee_experience",
        "hr_expense",
    ],
    "data": [
        "views/hr_expense_views.xml",
    ],
}
