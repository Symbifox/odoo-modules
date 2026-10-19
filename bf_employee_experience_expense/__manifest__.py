{
    "name": "Expérience employé — notes de frais",
    "summary": "Une dépense approuvée devient un usage d'avantage, au coût réel",
    "version": "18.0.1.0.1",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
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
* À l'approbation de la note de frais (bouton « Approuver », ou assistant des
  doublons), une ligne d'usage **confirmée** est créée pour chaque dépense
  rattachée à un avantage, d'origine « note de frais », avec le montant
  réellement engagé dans la devise de la société.
* Si l'approbation est retirée (note refusée, remise en brouillon, dépense
  retirée de la note), la ligne se retire aussi, avec une trace au fil de la
  dépense. Une nouvelle approbation en crée une nouvelle, au montant d'alors.
* Le lien est bidirectionnel : la ligne d'usage cite la dépense, la dépense
  cite la ligne. Une dépense ne peut pas produire deux usages.

⚠️ En Odoo 18, l'état d'une dépense est calculé depuis sa note de frais : il ne
passe jamais par `hr.expense.write`. Le pont écoute donc la note ;
la 18.0.1.0.1 rattrape les lignes que la 18.0.1.0.0 n'a jamais créées.

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
