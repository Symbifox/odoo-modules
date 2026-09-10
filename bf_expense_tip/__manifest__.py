# -*- coding: utf-8 -*-
{
    "name": "Pourboire",
    "summary": "Le pourboire d'un reçu de repas, saisi dans la dépense qui le "
               "porte, et retiré de l'assiette de taxes",
    "version": "18.0.1.0.1",
    "category": "Human Resources/Expenses",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ « Other proprietary » est la valeur que le schéma de manifeste d'Odoo
    # offre pour dire BUSL-1.1 : il n'a pas de valeur BUSL. C'est le fichier
    # LICENSE qui gouverne, pas cette ligne.
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "description": """
Pourboire
=========

Un reçu de restaurant porte deux montants de nature différente. Le repas est
une fourniture taxable : la TPS et la TVQ y sont calculées et imprimées. Le
pourboire, lui, est laissé librement et ne porte aucune taxe — c'est pour ça
qu'il est écrit sous le total du reçu, pas dedans.

Odoo ne connaît qu'un seul montant par dépense
----------------------------------------------

Le total saisi sur une `hr.expense` est réputé taxe incluse. Le pourboire s'y
retrouve donc dans l'assiette, et les taxes calculées dépassent celles du
reçu. Sur un reçu de 26,30 $ dont 3,10 $ de pourboire, avec la taxe groupée
« 14,975 % TPS+TVQ », Odoo annonce 3,43 $ de taxes là où le restaurateur en a
perçu 3,02 $. Ce ne sont pas 0,41 $ d'arrondi : ce sont 0,41 $ de crédits de
taxe sur les intrants réclamés en trop, sur ce seul repas.

Le remède natif d'Odoo 18 est le bouton **Fractionner la dépense**. Il donne
le bon résultat, mais il **copie** la dépense en autant d'enregistrements que
de lignes, il disparaît dès que la dépense est rattachée à un rapport, et il
demande de retaper la catégorie, les taxes et la description de chaque
morceau. Pour un repas d'affaires — le cas le plus courant et le plus
répétitif — c'est une demi-douzaine de gestes par reçu.

Ce que fait le module
---------------------

Un champ **Pourboire** sur la dépense, à côté du total. Le total reste ce que
la personne a réellement payé, donc ce qui lui est remboursé ne bouge pas.
Seule l'assiette de taxes change : elle devient *total moins pourboire*.

À la comptabilisation, l'écriture porte **deux lignes de charge** au lieu
d'une : le repas avec ses taxes, le pourboire sans aucune. Le pourboire va au
même compte que le repas — il fait partie des frais de représentation — à
moins qu'un compte lui soit désigné dans les réglages de comptabilité.

Les deux modes de paiement sont couverts : la facture fournisseur quand
l'employé avance l'argent, et l'écriture de paiement quand la dépense est
portée par une carte de la compagnie.

Ce qu'il ne fait pas
--------------------

* **La restriction de 50 %.** Revenu Québec limite à la moitié les CTI et RTI
  sur les frais de repas et de représentation, soit par un redressement en fin
  d'exercice, soit en ne réclamant que la moitié à chaque période. Ce module
  corrige l'assiette ; il ne choisit pas la méthode de réclamation, qui
  appartient au dossier fiscal de l'entreprise.
* **La lecture du reçu.** Le champ est prévu pour être rempli par une
  extraction automatique, mais le module ne lit aucune image.
* **Les catégories à coût fixe.** Une catégorie de dépense qui porte un prix
  standard (le kilométrage, par exemple) calcule son total elle-même : le
  champ y est masqué, comme l'est déjà le bouton de fractionnement d'Odoo.
""",
    "depends": [
        "hr_expense",
    ],
    "data": [
        "views/hr_expense_views.xml",
        "views/res_config_settings_views.xml",
    ],
}
