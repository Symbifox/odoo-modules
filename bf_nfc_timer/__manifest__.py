{
    "name": "Pastilles NFC : le chronomètre",
    "version": "18.0.1.0.1",
    "category": "Services/Timesheets",
    "summary": "Approcher le téléphone d'une pastille démarre ou arrête le chrono de la tâche",
    "description": """
Pastilles NFC : le chronomètre
==============================

Une pastille collée sur un dossier, un bureau, un portable. On approche le
téléphone : le chrono de cette tâche démarre. On le rapproche en partant : le
chrono s'arrête **et la feuille de temps est saisie**.

Ce dernier point n'est pas un détail
------------------------------------

Le chronomètre de la maison sépare deux gestes : ``stop_timer`` arrête et rend
de quoi peupler un dialogue, ``confirm_timesheet`` écrit la ligne. Un dialogue
suppose un navigateur ouvert et quelqu'un devant. Un tapotement n'a ni l'un ni
l'autre : il n'y a pas d'écran à remplir, et la personne est déjà en train de
mettre son manteau.

Ce module enchaîne donc les deux dans la même requête, avec l'arrondi configuré
dans les réglages. Un tapotement produit une ligne de feuille de temps, pas un
chrono en attente que personne ne viendra confirmer.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_nfc", "bf_timesheet_timer"],
    "data": [
        "data/bf_nfc_timer_data.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
