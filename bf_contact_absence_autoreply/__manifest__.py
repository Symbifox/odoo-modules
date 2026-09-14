# -*- coding: utf-8 -*-
{
    "name": "Symbifox Répondeur d'absence sur statut",
    # 18.0.1.0.0: premier jet. Le statut « Vacances » d'une fiche devient
    #   l'interrupteur du répondeur d'absence, au lieu d'un deuxième formulaire
    #   à remplir. Deux messages de maison posés à l'installation, un geste
    #   « Je m'absente » qui fait tout d'un coup, et le fuseau de la personne
    #   absente qui tranche les bornes de la période.
    "version": "18.0.1.0.0",
    "category": "Productivity/Discuss",
    "summary": "Se déclarer absent une fois, et que le répondeur, l'agenda et "
               "le rappel de retour suivent",
    "description": """
Répondeur d'absence sur statut
==============================

Le répondeur d'absence existait déjà et personne ne s'en servait, parce qu'il
fallait trois gestes préalables pour qu'il s'arme : cocher un réglage, avoir
déjà rédigé un gabarit, et nommer sa case d'agenda avec le bon mot. Mesuré sur
des bases vivantes, des semaines après la mise en service : **zéro
enregistrement**.

Ce que le module pose
---------------------

* **Le statut commande.** Une absence notée sur la fiche d'une personne de la
  maison arme son répondeur pour la période, et l'éteint à la fin. Rien à
  reporter dans un deuxième écran, rien à retaper.
* **« Je m'absente »**, un seul geste : les dates, la relève, le ton du
  message, le refus des invitations reçues pendant la période.
* **Deux messages de maison**, posés à l'installation et modifiables : celui
  qui annonce un délai (« je réponds moins vite ») et celui qui annonce une
  absence. Le premier est le défaut, parce qu'un message d'absence démenti par
  une vraie réponse coûte la crédibilité du suivant.
* **La relève nommée pour de vrai** : le nom et l'adresse sont écrits dans le
  message au moment où on arme, pas laissés à un marqueur qui rendrait une
  phrase trouée si personne n'est nommé.

Ce que le module ne fait pas
----------------------------

Il **ne répond jamais sans texte**. Sans message de maison ni gabarit
personnel, rien ne s'arme : un gabarit vide envoyé à un client coûte plus cher
qu'un silence.

Il **ne confie la boîte à personne** tout seul. Nommer une relève dans le
message et lui faire suivre le courrier sont deux gestes différents, et le
second reste explicite.

Il n'arme le répondeur **que pour les gens de la maison**. L'absence d'un
contact reste ce qu'elle est : une connaissance utile au moment d'écrire.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_contact_absence", "bf_email_management"],
    "data": [
        "security/ir.model.access.csv",
        "data/bf_absence_house_message_data.xml",
        "views/bf_absence_house_message_views.xml",
        "views/bf_partner_absence_views.xml",
        "views/bf_absence_me_wizard_views.xml",
        "views/menu_views.xml",
    ],
    "installable": True,
    "application": False,
}
