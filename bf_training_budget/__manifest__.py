{
    "name": "Registre de formation : ce qu'elle coûte vraiment",
    "version": "18.0.1.0.0",
    "category": "Human Resources",
    "summary": "Le coût des formations suivies porté au budget, et les heures dont "
               "le coût manque comptées comme manquantes plutôt que comme zéro",
    "description": """
Registre de formation : ce qu'elle coûte vraiment
=================================================

Le registre sait déjà ce qu'une formation a coûté : les heures, le taux horaire,
les cotisations de l'employeur, les frais. Le budget sait ce qu'on avait prévu.
Ce pont met les deux en face.

* **Une ligne budgétaire peut se nourrir du registre.** On y nomme des catégories
  de formation, et le réalisé de la ligne prend le coût des formations suivies
  dans la période, sans écriture comptable.
* **L'engagé prend les obligations à venir.** Ce qui est dû et pas encore suivi
  a un coût prévisible : il apparaît comme engagé, pas comme une surprise de fin
  d'exercice.

Un refus repris, parce que les deux modules le partagent déjà
--------------------------------------------------------------

Le budget porte ``unvalued_hours`` : « heures saisies dont le coût est nul ».
Le registre porte ``is_complete`` et ``missing_info``. Les deux disent la même
chose — **une valeur absente n'est pas une valeur nulle** — et ce pont les
branche l'un sur l'autre plutôt que d'inventer un troisième vocabulaire.

Une formation à laquelle il manque ses heures, son taux horaire ou sa base
d'admissibilité **n'entre pas dans le réalisé**. Ses heures sont ajoutées aux
heures non valorisées de la ligne, là où le budget les signale déjà. Un budget
de formation qui avale les trous produit un chiffre faux qui a l'air juste, et
c'est le chiffre que quelqu'un portera à une décision.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_training", "bf_budget"],
    "data": [
        "views/budget_line_views.xml",
    ],
    "installable": True,
    "application": False,
}
