# -*- coding: utf-8 -*-
{
    "name": "Démarchage — réponses courriel",
    # 18.0.1.1.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    "version": "18.0.1.1.0",
    "category": "Sales/CRM",
    "summary": "Reconnaît dans les courriels reçus les réponses des cibles de démarchage",
    "description": """
Démarchage — réponses courriel
==============================

Passerelle entre « Campagnes de démarchage » et « Gestion des courriels ».

Une action planifiée parcourt les courriels **reçus** archivés dans `bf.email`
et, lorsque l'expéditeur correspond à l'adresse d'une cible active, crée
l'interaction entrante correspondante.

Conséquence directe : l'option « Arrêter à la première réponse » n'a plus
besoin d'une saisie manuelle — une cible qui répond sort d'elle-même de la
cadence, et se retrouve dans le filtre « Ont répondu ».

Le rapprochement se fait sur l'adresse normalisée, ne remonte jamais plus loin
qu'un filigrane de date, et ne crée jamais deux fois la même interaction.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_outreach", "bf_email_management"],
    "data": [
        "data/outreach_email_cron.xml",
    ],
    "installable": True,
    "auto_install": True,
}
