{
    "name": "Fédération",
    "version": "18.0.1.1.0",
    "category": "Project",
    "summary": "Fédérer deux instances : partager des tâches d'un Symbifox à l'autre, "
               "avec l'état, l'échéance, les messages et les pièces jointes qui suivent",
    "description": """
Fédération
==========

Deux instances qui se font confiance échangent des tâches sans qu'une personne
ait besoin d'un compte chez l'autre. Chaque instance émet et reçoit.

* **Jumelage par invitation**, réservé aux administrateurs : l'une génère un
  code, l'autre l'accepte ; un secret partagé naît de l'échange et signe ensuite
  chaque message (HMAC-SHA256, horodatage, nonce).
* **Partage d'une tâche** : une action de masse ou un champ sur la tâche. Le
  miroir apparaît chez le pair, dans un projet fermé, assigné à la personne
  choisie par le pair.
* **Ce qui voyage** : nom, description réduite en texte, jour d'échéance,
  priorité, état, messages Discussions, pièces jointes sous un plafond ;
  les notes internes seulement si le pair le décide pour son propre côté.
* **Ce qui revient** : l'état (retourné : « Attente - Client » chez l'émetteur
  devient « En cours » chez le receveur), le jour d'échéance, les messages.
* **Un marqueur d'exclusion** : un message ou une note qui commence par 🔒
  ou [privé] reste chez son auteur.
* **Rien ne se supprime** : retirer le partage archive le miroir.
* **Boîte de sortie avec reprise** : chaque envoi est journalisé et rejoué en
  cas de panne du pair.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["project", "mail"],
    "data": [
        "security/federation_security.xml",
        "security/ir.model.access.csv",
        "data/federation_cron.xml",
        "views/federation_link_views.xml",
        "views/federation_outbox_views.xml",
        "views/federation_peer_views.xml",
        "views/federation_wizard_views.xml",
        "views/project_project_views.xml",
        "views/project_task_views.xml",
        "views/federation_menu_views.xml",
        "data/federation_actions.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
