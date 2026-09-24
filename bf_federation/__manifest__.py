{
    "name": "Fédération",
    "version": "18.0.1.5.2",
    "category": "Project",
    "summary": "Fédérer deux instances : partager un objet d'un Symbifox à l'autre, avec "
               "les messages et les pièces jointes qui suivent. Le socle porte la tâche ; "
               "les satellites ajoutent les livrables, les ordres du jour, les cartographies",
    "description": """
Fédération
==========

Deux instances qui se font confiance échangent des objets sans qu'une personne
ait besoin d'un compte chez l'autre. Chaque instance émet et reçoit.

Le socle porte le transport et **un genre, la tâche**. Les autres arrivent par
des satellites (`bf_federation_document`, `_meeting`, `_process`, `_discuss`)
qui remplissent le contrat de `federation.federable`.

* **Jumelage par invitation**, réservé aux administrateurs : l'une génère un
  code, l'autre l'accepte ; un secret partagé naît de l'échange et signe ensuite
  chaque message (HMAC-SHA256, horodatage, nonce).
* **Partage d'une tâche** : une action de masse ou un champ sur la tâche. Le
  miroir apparaît chez le pair, dans un projet fermé.
* **À qui, chez le pair** : l'émetteur peut adresser la tâche à une personne
  qu'il connaît déjà de l'organisation du pair. C'est une proposition, pas une
  assignation : le receveur la résout avec sa propre table des personnes
  appariées, retombe sur son repli quand il ne la reconnaît pas, et garde le
  dernier mot. Aucun annuaire ne traverse, dans aucun des deux sens.
* **Le pair annonce ce qu'il sait recevoir** : le jumelage et le contact rendent
  la liste des genres acceptés, et un genre que l'autre côté ne connaît pas est
  refusé plutôt que traduit de travers.
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
