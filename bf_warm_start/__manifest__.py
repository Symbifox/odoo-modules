{
    "name": "Warm Start",
    # 18.0.1.0.0 : première version. Un processus Odoo neuf relisait,
    #   à sa première page, tous les .po de la langue de l'usager pour calculer
    #   l'empreinte des traductions du client web : environ 10 s sur une base de 545
    #   modules (457 fichiers), payées par la personne qui ouvrait la page, et cela
    #   pour chacun des workers après chaque redémarrage. Le module charge
    #   ces traductions une fois, au chargement du registre. Ajouté aux
    #   `server_wide_modules`, il fait aussi précharger la base par le maître
    #   préfork avant la création des workers, qui en héritent ; sans `db_name`,
    #   qui ferait créer la base si elle manquait au démarrage.
    "version": "18.0.1.0.0",
    "summary": "Load web client translations once, before the workers fork, "
               "so the first page after a restart is fast",
    "category": "Technical",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["web"],
    "data": [],
    "post_load": "post_load",
    "installable": True,
    "application": False,
}
