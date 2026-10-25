{
    "name": "Abonnements — notes de frais",
    "version": "18.0.1.0.1",
    "category": "Accounting/Accounting",
    "summary": "Un abonnement payé en note de frais compte dans les coûts de l'abonnement",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": False,
    "depends": ["bf_subscription", "hr_expense"],
    "description": """
Abonnements — notes de frais
============================

Un abonnement payé à la carte d'un employé arrive en note de frais, pas en
facture fournisseur. Le registre des abonnements ne lisait que les factures :
un tel abonnement n'avait aucun coût cumulé et passait pour dormant, puisqu'il
ne recevait jamais de facture.

Ce satellite ajoute un champ Abonnement sur la dépense. Une dépense approuvée
ou payée qui y est rattachée compte dans le total payé de l'abonnement et dans
la date de sa dernière facture ; un bouton ouvre ses dépenses.

**La dépense, jamais la pièce de la note**
  La note de frais produit une pièce fournisseur au nom de l'employé, qui
  regroupe toutes ses dépenses de la période. Le lien se pose sur chaque
  dépense : rattacher la pièce compterait aussi les dépenses des autres
  abonnements.
""",
    "data": [
        "views/hr_expense_views.xml",
        "views/subscription_subscription_views.xml",
    ],
}
