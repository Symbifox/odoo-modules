{
    "name": "Registre de formation : raccord eLearning",
    "version": "18.0.1.0.1",
    "category": "Human Resources",
    "summary": "Une activité du registre adossée à un cours en ligne : la vraie date, "
               "les heures, et la dérive du contenu qui se voit",
    "description": """
Registre de formation : raccord eLearning
=========================================

Le lecteur d'Odoo joue les contenus ; le registre tient la preuve. Ce pont fait
passer l'un dans l'autre, et corrige au passage trois choses que le natif ne
sait pas dire.

* **Une activité peut être adossée à un cours.** L'inscription, l'avancement et
  la complétion suivent.
* **La complétion crée une réalisation datée du jour où elle arrive.** Le natif
  ne garde aucune date de complétion : ni `slide.channel.partner` ni
  `slide.slide.partner` n'en portent une. Ici, la ligne du registre est écrite
  au moment où la complétion se produit, donc elle est datée pour de bon.
* **Les heures viennent de la durée des contenus**, faute de mieux, et la ligne
  est marquée incomplète si le cours n'en déclare aucune. Un cours sans durée ne
  vaut pas zéro heure, il vaut « on ne sait pas ».
* **La dérive du contenu se voit.** Quand le cours gagne ou perd des contenus
  publiés après la dernière montée de version, l'activité est signalée. Le
  registre **ne décide pas** à la place de la personne : corriger une coquille ne
  devrait faire refaire le cours à personne. Il montre l'écart et laisse monter
  la version.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_training", "website_slides"],
    "data": [
        "views/training_activity_views.xml",
        "views/training_assignment_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
