{
    "name": "Gestes par pastille NFC",
    "version": "18.0.2.3.1",
    "category": "Productivity",
    "summary": "Une pastille, un geste, et le nom de la personne qui a tape",
    "description": """
Gestes par pastille NFC
=======================

Une pastille NFC ne declenche rien toute seule. Elle porte quelques octets, et
dans la pratique un seul format traverse les deux plateformes : un lien. Le
telephone l'ouvre, et c'est le serveur qui agit.

Ce module fournit le serveur : un registre de pastilles, un catalogue de gestes,
un journal de ce qui a ete tape, et **trois portes** pour y entrer.

La pastille ne porte jamais de secret
-------------------------------------

Elle porte un code court et public. Ce qui differe d'une porte a l'autre, c'est
d'ou vient l'identite :

* **l'application** : le telephone deja appairie presente son jeton porteur, et
  le geste est fait au nom de cette personne;
* **le navigateur** : la session Odoo ouverte fournit l'identite, et tout geste
  qui ecrit passe par une page de confirmation a un bouton;
* **la pastille signee** (NTAG 424 DNA) : la puce signe chaque tapotement en
  AES-CMAC avec un compteur qui ne remonte jamais, et le geste est fait au nom
  d'un compte designe sur la pastille. C'est la seule facon honnete de faire
  agir une pastille remise a quelqu'un qui n'a pas de compte.

Ce que le module refuse de faire
--------------------------------

* **Agir sur un GET non signe.** Un apercu de lien, un antipourriel ou un
  scanner ouvrent les URL sans que personne n'ait touche a rien. La page de
  confirmation n'est pas de la politesse, c'est la barriere.
* **Repondre 200 a un code inconnu.** Une pastille gravee qui atterrit ailleurs
  ne se corrige plus : le code inconnu rend un 404 franc.
* **Executer deux fois le meme tapotement.** Un doigt qui hesite, une double
  distribution d'Android : deux lectures rapprochees rendent le resultat de la
  premiere sans rejouer le geste.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["base", "web", "mail", "portal"],
    "data": [
        "security/bf_nfc_groups.xml",
        "security/ir.model.access.csv",
        "data/bf_nfc_gesture_data.xml",
        "data/bf_nfc_cron.xml",
        "views/bf_nfc_gesture_views.xml",
        "views/bf_nfc_tag_views.xml",
        "views/bf_nfc_tap_views.xml",
        "views/bf_nfc_menus.xml",
        "views/bf_nfc_device_views.xml",
        "views/bf_nfc_admin_views.xml",
        "views/bf_nfc_template_views.xml",
        "data/bf_nfc_template_data.xml",
        "views/portail_templates.xml",
        "report/bf_nfc_tag_label.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": True,
    "auto_install": False,
}
