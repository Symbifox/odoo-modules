{
    "name": "Registre de formation",
    "version": "18.0.1.1.0",
    "category": "Human Resources",
    "summary": "Qui doit quelle formation, pour quand, prouvé par quelle pièce, "
               "valide jusqu'à quand. Le registre nominatif que le lecteur eLearning "
               "ne tient pas",
    "description": """
Registre de formation
=====================

Un module eLearning est un **lecteur** : il joue des contenus. Ce module est un
**registre** : il tient l'obligation, la preuve et l'échéance.

* **L'activité** est le catalogue : un titre, une catégorie, un mode, une durée
  prévue, un organisme, une validité en mois.
* **L'exigence** est la règle : qui doit quoi (une activité précise, ou un nombre
  d'heures dans des catégories), à partir de quel déclencheur (l'embauche, une
  date fixe), avec quel délai, à quelle fréquence, et sur quelle base
  réglementaire citée au long.
* **L'obligation** est la règle appliquée à une personne : une ligne par personne
  visée, avec sa date d'échéance et son état, tenue à jour par une tâche
  planifiée.
* **La réalisation** est la preuve : la vraie date, les heures, l'organisme, le
  numéro, la pièce jointe reçue de l'extérieur, et la date d'expiration.
* **Le plan de formation** porte la consultation et sa preuve, parce que
  certaines activités ne sont admissibles que dans un plan.
* **L'assignation** relance la personne avant l'échéance.

Trois principes de conception, tirés de ce que le natif fait mal
----------------------------------------------------------------

1. **Un état qui dépend de la date du jour se porte par une tâche planifiée,
   jamais par un champ calculé stocké.** Un champ stocké qui ne dépend que d'une
   date ne se recalcule pas parce que le temps passe : il affiche « valide » pour
   toujours.
2. **Une réalisation ne s'écrase pas.** Chaque renouvellement est une ligne de
   plus. Trois secourismes en neuf ans laissent trois lignes.
3. **Ce qui manque ne vaut pas zéro.** Une réalisation sans heures ou sans coût
   horaire est signalée comme incomplète, jamais comptée pour zéro.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["base", "mail", "hr", "hr_hourly_cost"],
    "data": [
        "security/training_security.xml",
        "security/ir.model.access.csv",
        "data/training_category_data.xml",
        "data/training_cron.xml",
        "views/training_category_views.xml",
        "views/training_activity_views.xml",
        "views/training_plan_views.xml",
        "views/training_requirement_views.xml",
        "views/training_obligation_views.xml",
        "views/training_record_views.xml",
        "views/training_assignment_views.xml",
        "views/hr_employee_views.xml",
        "views/training_menus.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
