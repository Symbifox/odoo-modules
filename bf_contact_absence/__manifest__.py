# -*- coding: utf-8 -*-
{
    "name": "Symbifox Absences des contacts",
    # 18.0.1.0.0: premier jet. Une absence est une période datée posée sur la
    #   fiche contact, et elle se voit au moment où on écrit : bandeau dans le
    #   composeur complet (qui couvre aussi la boîte unifiée, puisque la
    #   réponse d'un courriel ouvre le wizard natif) et dans le composeur du
    #   chatter. La relève est nommée, le report à la date de retour passe par
    #   `mail.scheduled.message` (natif en 18), et un rappel de reprise est
    #   posé au lendemain du retour.
    "version": "18.0.1.0.0",
    "category": "Sales/CRM",
    "summary": "Savoir qu'un contact est absent avant de lui écrire, de lui "
               "texter ou de l'appeler",
    "description": """
Absences des contacts
=====================

Un contact part deux semaines et personne ne s'en souvient le mardi suivant.

Ce que le module pose
---------------------

* **Une période datée sur la fiche contact** : du, au, la nature (vacances,
  congé, fermeture, formation), et surtout **la relève**, c'est-à-dire à qui
  écrire pendant ce temps-là.
* **Un bandeau au moment où on écrit**, pas un rapport qu'on lit après coup.
  Il apparaît dans le composeur de courriel complet et dans le composeur du
  chatter, il nomme la personne absente, la date de retour et la relève.
* **Le report d'un clic** : « Programmer à son retour » range le message dans
  les envois différés d'Odoo, au matin du lendemain du retour. Personne ne veut
  être le premier courriel d'une boîte à quatre cents non lus.
* **Le rappel de reprise** : une activité « prendre des nouvelles » posée au
  lendemain du retour, quand on la demande.
* **La fermeture d'entreprise** : une absence posée sur une société avertit
  pour tous ses contacts, avec un libellé qui dit « fermé » et non « absent ».

Ce que le module ne fait pas
----------------------------

Il **n'empêche jamais** un envoi. Écrire à quelqu'un en vacances est souvent
exactement ce qu'on veut, pour que ça l'attende à son retour. Le bandeau
informe et propose ; il ne barre rien.

Il ne stocke **aucun motif de santé** et jamais le texte libre d'un répondeur
d'absence : une fenêtre, une nature choisie dans une liste courte, une relève.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["contacts", "mail"],
    "data": [
        "security/bf_absence_security.xml",
        "security/ir.model.access.csv",
        "data/bf_absence_cron.xml",
        "views/bf_partner_absence_views.xml",
        "views/bf_absence_closure_wizard_views.xml",
        "views/res_partner_views.xml",
        "views/mail_compose_message_views.xml",
        "views/menu_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_contact_absence/static/src/scss/absence.scss",
            "bf_contact_absence/static/src/js/absence_chatter_patch.js",
            "bf_contact_absence/static/src/xml/absence_chatter.xml",
        ],
    },
    "installable": True,
    "application": False,
}
