{
    "name": "Pastilles NFC : la présence à une rencontre",
    "version": "18.0.1.0.1",
    "category": "Productivity",
    "summary": "Taper la pastille de la salle note sa présence à la rencontre qui commence",
    "description": """
Pastilles NFC : la présence à une rencontre
===========================================

Une pastille sur la table ou la porte de la salle de réunion. En arrivant, on
approche le téléphone : on est noté « Présent » dans les présences de la
rencontre en cours, celles qui alimentent le compte rendu.

* **Pastille sans fiche** : la rencontre en cours à laquelle la personne est
  invitée (invités de la rencontre, participants de son événement d'agenda, ou
  organisateur). S'il y en a deux, l'écran demande laquelle.
* **Pastille sur une rencontre** : celle-là, invité ou non. Quelqu'un qui arrive
  sans invitation est quand même noté : le compte rendu dit qui était là.

Une présence se note de trente minutes avant le début à quinze minutes après la
fin prévue (une heure si la durée n'est pas saisie). Une pastille signée ne note
pas de présence : elle agit au nom d'un compte désigné, pas de la personne.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_nfc", "bf_meeting"],
    "data": [
        "data/bf_nfc_meeting_data.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": True,
}
