{
    "name": "Relations de travail : côté employeur",
    "summary": "Obligations et rappels, liste d'ancienneté affichée, affichages de "
               "poste et mouvements, préparation de la remise, comité",
    "version": "18.0.1.0.0",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "description": """
Relations de travail : côté employeur
=====================================

Le socle décrit ce que la relation **est**. Ce greffon porte ce que l'employeur
**doit faire**, et quand.

Ce qu'il ajoute
---------------

* **Les obligations** (`bf.labour.obligation`) : ce que la convention impose à
  l'employeur, avec son échéance et son rappel. Transmettre la liste
  d'ancienneté, afficher un poste dans les délais, convoquer le comité, remettre
  les cotisations. Un traitement planifié pose l'activité avant l'échéance,
  parce qu'une obligation dont personne n'est averti est une obligation manquée.
* **La liste d'ancienneté affichée** (`bf.labour.seniority.list`), qui est une
  **photo figée**, pas une vue. C'est la distinction qui compte : les rangs se
  contestent contre la liste affichée à une date, pas contre l'état d'aujourd'hui.
  Une liste publiée ne se récrit plus.
* **Les affichages de poste et les mouvements** (`bf.labour.posting`), avec les
  mises en candidature classées par ancienneté **au moment de l'affichage**.
  Affichage ordinaire, supplantation, rappel : ce sont trois portes différentes
  vers le même poste.
* **La préparation de la remise** : la règle de cotisation de la convention
  s'applique à une assiette saisie, ligne par ligne. Le module ne devine pas
  l'assiette, il l'attend ; ce qu'il garantit, c'est que la règle est appliquée
  uniformément.
* **Le comité de relations de travail** (`bf.labour.committee`) et ses
  rencontres, parce que la plupart des conventions en imposent la fréquence.

Ce qu'il ne fait pas
--------------------

* Il ne crée pas un second modèle de grief. Le grief vit au socle, et les deux
  côtés lisent le même dossier.
* Il ne calcule pas la paie. L'assiette de la cotisation est saisie ; c'est la
  règle qui est appliquée, pas le salaire qui est produit.
""",
    "depends": [
        "bf_labour_relations",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/employer_security.xml",
        "data/ir_cron.xml",
        "views/obligation_views.xml",
        "views/seniority_list_views.xml",
        "views/posting_views.xml",
        "views/committee_views.xml",
        "views/dues_remittance_views.xml",
        "views/menuitems.xml",
    ],
}
