{
    "name": "Sub-tasks Always Visible",
    "summary": "Sub-tasks appear in the kanban and list views of their project "
               "like any other task, and the eye that hid them is gone.",
    # 18.0.1.0.0 : Odoo 18 cache une sous-tâche rangée dans le même
    #   projet que son parent (`project.task.display_in_project` à faux) et ne
    #   la montre qu'à qui clique l'œil de la fiche ; les sept actions de
    #   `project` et les compteurs des cartes de projet portent la même feuille
    #   de domaine. Ici le drapeau vaut toujours vrai, l'œil disparaît, et un
    #   filtre « Sans les sous-tâches » (éteint par défaut) replie les
    #   sous-tâches quand la vue est trop chargée. La pose retourne les fiches
    #   déjà cachées (crochet d'installation).
    "version": "18.0.1.0.0",
    "category": "Services/Project",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "depends": ["project"],
    "data": [
        "views/project_task_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
    "auto_install": False,
}
