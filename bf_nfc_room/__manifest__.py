{
    "name": "Pastilles NFC : la salle de réunion",
    "version": "18.0.1.1.0",
    "category": "Productivity/Calendar",
    "summary": "La pastille de la porte : libre ou occupée, la prendre, confirmer, la libérer",
    "description": """
Pastilles NFC : la salle de réunion
===================================

Une pastille sur la porte de la salle. On approche le téléphone :

* **Libre** : « Prendre 30 min », « Prendre 1 h », bornés par la réservation
  suivante. La réservation naît dans l'agenda, déjà confirmée.
* **Réservée par vous et sur le point de commencer** : « J'arrive » confirme.
* **Occupée par vous** : « Libérer la salle » la rend tout de suite.
* **Occupée par quelqu'un d'autre** : l'écran dit par qui et jusqu'à quand.

Une réservation que personne ne confirme dans les dix minutes (réglable par salle)
perd sa salle : l'événement reste dans l'agenda, la salle redevient libre, et une
note le dit sur l'événement. Deux réservations de la même salle ne peuvent pas se
chevaucher, qu'elles viennent d'une pastille ou de l'agenda.

Une pastille signée ne prend pas de salle, et un tapotement sans réseau non plus :
une salle se prend sur place et maintenant.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_nfc", "calendar"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_nfc_room_rules.xml",
        "data/bf_nfc_room_data.xml",
        "views/bf_nfc_room_views.xml",
        "data/bf_nfc_room_gabarits.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
