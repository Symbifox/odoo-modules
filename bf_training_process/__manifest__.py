{
    "name": "Registre de formation : entraînement à la tâche",
    "version": "18.0.1.0.0",
    "category": "Human Resources",
    "summary": "La formation accrochée à l'étape de processus qu'elle enseigne, et le "
               "couloir qui dit enfin qui joue le rôle",
    "description": """
Registre de formation : entraînement à la tâche
===============================================

Une cartographie de processus dit **ce qui se fait** et **par quel rôle**. Le
registre dit **qui doit savoir le faire**. Ce pont les relie, et comble au
passage le trou entre les deux.

* **Le couloir gagne ses personnes.** Une carte nomme le rôle, « Opérateur »,
  « Administration de l'identité », mais personne n'y dit qui joue ce rôle. Le
  pont l'ajoute, et une exigence peut alors viser un couloir plutôt qu'une liste
  de noms recopiée à la main.
* **L'étape gagne ses formations.** Depuis un nœud de la carte, on voit ce qu'il
  faut savoir pour le tenir, et de là qui est à jour.
* **L'entraînement à la tâche se consigne pour de vrai** : le formateur,
  l'apprenant, l'étape travaillée, et **la confirmation des deux**. Une formation
  que seul le formateur déclare n'est pas une formation reçue.

Pourquoi la double confirmation
-------------------------------

C'est la forme de preuve que l'entraînement à la tâche exige, et c'est aussi
celle que la carte elle-même emploie déjà pour valider une étape : le
propriétaire et l'exécutant se prononcent chacun. Le pont reprend ce geste
plutôt que d'en inventer un autre.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_training", "bf_process"],
    "data": [
        "views/process_lane_views.xml",
        "views/process_node_views.xml",
        "views/training_activity_views.xml",
        "views/training_requirement_views.xml",
        "views/training_record_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
