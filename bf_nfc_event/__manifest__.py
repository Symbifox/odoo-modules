{
    "name": "Pastilles NFC : la présence à une séance",
    # 18.0.1.0.2: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    "version": "18.0.1.0.2",
    "category": "Marketing/Events",
    "summary": "Taper la pastille de la salle note la présence à la formation ou à l'événement en cours",
    "description": """
Pastilles NFC : la présence à une séance
========================================

Une pastille sur la porte de la salle de formation. En arrivant, chacun approche
son téléphone : sa présence est notée à la séance en cours dans cette salle,
avec l'heure. Plus de feuille qui circule, plus de liste recopiée après coup.

* **Pastille sur le lieu** (la fiche du lieu de l'événement) : la séance en cours
  à cet endroit. S'il y en a deux, l'écran demande laquelle.
* **Pastille sur la séance** : cette séance-là, seulement pendant qu'elle a lieu.

L'inscription de la personne passe à « Présent ». Sans inscription, elle est
créée : on note qui est venu, pas seulement qui s'était inscrit. Avec le registre
de formation, la présence y est écrite toute seule.

Ce que le module refuse
-----------------------

* **Une présence au nom d'un compte générique.** Une pastille signée agit au nom
  d'un compte désigné : la présence se note avec l'application ou une session.
* **Une présence hors séance.** Une heure avant le début, pas plus tôt ; une
  demi-heure après la fin, pas plus tard.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_nfc", "event"],
    "data": [
        "data/bf_nfc_event_data.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": True,
}
