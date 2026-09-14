{
    "name": "Pastilles NFC : signaler un problème",
    "version": "18.0.1.0.1",
    "category": "Services/Helpdesk",
    "summary": "Une pastille sur l'imprimante ou le serveur d'un client ouvre un billet déjà rempli",
    "description": """
Pastilles NFC : signaler un problème
====================================

Une pastille collée sur l'imprimante, le serveur, la borne, la machine d'un
client. On approche le téléphone, on écrit une phrase, et le billet naît déjà
rempli : le client, l'équipement, l'endroit, qui a signalé et quand.

* **Sur une fiche client** : le billet est au nom de ce client.
* **Sur n'importe quelle fiche qui a un client** (un équipement, un projet) : le
  billet prend son client, et le nom de la fiche dans son titre.
* **Dans un menu** : ``{"texte_fixe": "Plus de papier"}`` crée le billet sans
  rien demander. Trois boutons « Plus de papier », « Bourrage », « Autre » sur
  une seule pastille.

Le billet passe par le canal « Pastille », pour qu'on les retrouve et qu'on
mesure ce qu'elles produisent. Une pastille signée peut signaler au nom du
compte qu'elle désigne : c'est le cas du client qui n'a pas de compte.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_nfc", "helpdesk_mgmt"],
    "data": [
        "data/bf_nfc_helpdesk_data.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": True,
}
