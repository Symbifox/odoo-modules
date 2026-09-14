{
    "name": "Pastilles NFC : le prêt d'équipement",
    "version": "18.0.1.0.1",
    "category": "Productivity",
    "summary": "Une pastille sur le portable ou le projecteur : on sait qui l'a, depuis quand",
    "description": """
Pastilles NFC : le prêt d'équipement
====================================

Une pastille collée sur un portable, un projecteur, une caméra, une clé. On
approche le téléphone :

* l'équipement est libre : **vous le prenez** ;
* vous l'avez : **vous le rendez** ;
* quelqu'un d'autre l'a : l'écran le nomme, et propose de le reprendre.

Un seul tapotement dans les deux cas courants, sans bouton. Le journal de
chaque équipement dit qui l'a eu, quand, et combien de temps. Un rappel part à
la personne qui le garde au-delà du délai choisi.

Ce que le module refuse
-----------------------

* **Prêter au nom d'un compte générique.** Une pastille signée agit au nom d'un
  compte désigné, pas de celui qui tient le téléphone : le prêt se joue avec
  l'application ou une session.
* **Réécrire l'heure.** Un tapotement fait sans réseau garde l'heure notée par le
  téléphone, bornée par le socle.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_nfc", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "security/bf_nfc_loan_rules.xml",
        "data/bf_nfc_loan_data.xml",
        "views/bf_nfc_loan_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
