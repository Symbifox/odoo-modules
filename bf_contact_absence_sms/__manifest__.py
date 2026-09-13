# -*- coding: utf-8 -*-
{
    "name": "Symbifox Absences des contacts : Messagerie SMS",
    # 18.0.1.0.0: premier jet. Le bandeau d'absence dans l'en-tête d'une
    #   conversation SMS, dès qu'elle est ouverte.
    "version": "18.0.1.0.0",
    "category": "Sales/CRM",
    "summary": "Voir qu'un contact est absent avant de lui texter",
    "description": """
Absences des contacts : Messagerie SMS
======================================

Le socle avertit là où on écrit un courriel. Ce pont fait la même chose dans la
Messagerie SMS : un bandeau dans l'en-tête de la conversation, dès qu'elle est
ouverte, avant d'avoir tapé quoi que ce soit.

Rien n'est bloqué : texter quelqu'un en vacances est parfois exactement ce
qu'on veut.

Pourquoi un module à part
-------------------------

Le patron du bandeau **hérite** du patron de la Messagerie. Un héritage de
patron ne peut pas être conditionnel : il faut que le module hérité soit là,
sinon le paquet d'actifs entier tombe. C'est donc la dépendance qui décide du
découpage, pas une préférence.

Aucune méthode neuve côté serveur : la Messagerie appelle
``bf.partner.absence.hint_for_thread``, la seule porte publique du socle.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_contact_absence", "bf_sms_archive"],
    "assets": {
        "web.assets_backend": [
            "bf_contact_absence_sms/static/src/js/absence_sms_patch.js",
            "bf_contact_absence_sms/static/src/xml/absence_sms.xml",
        ],
    },
    "installable": True,
    "application": False,
}
