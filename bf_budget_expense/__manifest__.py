{
    "name": "Budgets opérationnels — notes de frais",
    "version": "18.0.1.0.0",
    "category": "Accounting/Accounting",
    "summary": "Les notes de frais approuvées entrent dans l'engagé avant d'être comptabilisées",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "depends": ["bf_budget", "hr_expense"],
    "description": """
Budgets opérationnels — notes de frais
======================================

Une note de frais approuvée est une dépense décidée : quelqu'un l'a validée, et
l'argent sortira. Tant qu'elle n'est pas comptabilisée, le socle ne la voit pas.
Ce satellite l'ajoute à l'engagé du poste où elle tombera.

**Le garde est le lien, jamais l'état**
  Dans Odoo, une note comptabilisée mais pas encore payée garde l'état
  « Approuvée ». Filtrer sur l'état la compterait deux fois : une fois dans le
  réalisé, par son écriture, et une fois dans l'engagé. Le module ne retient que
  les notes approuvées dont la pièce n'a AUCUNE écriture comptabilisée.

**Le montant est celui qui tombera sur le compte de charge**
  Les taxes récupérables vont à leurs propres comptes : elles ne sont pas une
  charge et n'entrent pas dans l'engagé. Une taxe non récupérable, elle, tombe
  sur le compte de charge et y entre. Le calcul passe par le moteur de taxes
  d'Odoo, le même que celui qui produira l'écriture.

**Le soumis s'affiche, il ne s'engage pas**
  Une note soumise mais pas encore approuvée n'est pas une décision. Elle est
  montrée à part, pour information, et n'entre dans aucun montant.
""",
    "data": [
        "views/bf_budget_line_views.xml",
    ],
}
