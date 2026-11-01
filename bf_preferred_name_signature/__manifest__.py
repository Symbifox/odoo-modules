# -*- coding: utf-8 -*-
{
    "name": "Symbifox Noms d'usage et pronoms — pont Signatures",
    # 18.0.1.0.0: premier jet. Les pronoms de la
    #   signature de courriel étaient un champ à part sur l'usager, rempli à part
    #   pour chaque usager. Ils deviennent ceux de la fiche :
    #   une seule saisie, que la signature, Gen et le portail lisent.
    "version": "18.0.1.0.0",
    "category": "Productivity/Discuss",
    "summary": "Les pronoms de la signature sont ceux de la fiche de la "
               "personne",
    "description": """
Pont Signatures
===============

``bf_multi_company_email`` affiche des pronoms à côté du nom dans la signature
de courriel, depuis un champ de l'usager. ``bf_preferred_name`` porte les
pronoms sur la fiche de la personne, où elle les saisit elle-même. Deux champs
pour la même chose finissent par se contredire.

Ce pont, installé de lui-même quand les deux modules sont là :

* fait du champ « Pronoms » de la signature **le champ de la fiche** : le
  modifier dans ses préférences, sur sa fiche ou au portail, c'est le même ;
* **refait la signature** quand les pronoms changent, d'où que vienne le
  changement ;
* à son installation, **reprend** sur la fiche les pronoms déjà saisis pour la
  signature, sans jamais écraser des pronoms déjà posés sur la fiche ;
* à sa désinstallation, **rend** à la signature les pronoms de la fiche, pour
  qu'elle ne revienne pas à ceux d'avant.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_preferred_name", "bf_multi_company_email"],
    "data": [
        "views/res_users_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "auto_install": True,
    "application": False,
}
