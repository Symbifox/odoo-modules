# -*- coding: utf-8 -*-
{
    "name": "Démarchage — appels journalisés",
    "version": "18.0.1.0.0",
    "category": "Sales/CRM",
    "summary": "Rapproche les appels réellement passés avec les cibles de démarchage",
    "description": """
Démarchage — appels journalisés
===============================

Passerelle entre « Campagnes de démarchage » et l'archive d'appels de
`bf_sms_archive`.

Une action planifiée rapproche les appels de `call.archive.call` avec les
cibles, sur le numéro en format international, et crée l'interaction
correspondante — avec la vraie durée, le bon sens et un résultat déduit du
type d'appel.

Tout téléphone logiciel qui alimente la même archive d'appels est couvert sans
travail supplémentaire : un appel passé depuis le navigateur se retrouve
journalisé sur la cible sans un clic de plus.

La saisie manuelle reste possible : elle ne fait plus double emploi, le
rapprochement ne crée jamais deux fois le même appel.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://bluefoxconsultant.com",
    "license": "LGPL-3",
    "depends": ["bf_outreach", "bf_sms_archive"],
    "data": [
        "data/outreach_call_cron.xml",
    ],
    "installable": True,
    "auto_install": True,
}
