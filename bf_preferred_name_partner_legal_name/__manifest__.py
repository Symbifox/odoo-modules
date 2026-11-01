# -*- coding: utf-8 -*-
{
    "name": "Symbifox Noms d'usage et pronoms — pont Partner Legal Name",
    # 18.0.1.0.0: premier jet. Le « Legal name » de partner_legal_name est
    #   fait pour les sociétés : lisible par tous les internes et suivi au fil.
    #   Il restait affiché sur la fiche d'une personne, à côté du nom légal
    #   protégé de bf_preferred_name. Le pont le masque pour les particuliers.
    #   Et le ferme (contrainte), sauf sur la fiche d'une
    #   société Odoo ; à l'installation, les valeurs déjà tapées sur des
    #   personnes passent au nom légal protégé.
    "version": "18.0.1.0.0",
    "category": "Productivity/Contacts",
    "summary": "Un seul nom légal sur la fiche d'une personne : celui qui est "
               "protégé",
    "description": """
Pont Partner Legal Name
=======================

``partner_legal_name`` ajoute aux fiches un « Legal name » pensé pour la raison
sociale d'une société : il n'est réservé à aucun groupe et ses changements sont
suivis au fil. Sur la fiche d'une personne, il invitait à y taper un nom légal
que tous les internes auraient lu.

Ce pont, installé de lui-même quand les deux modules sont là :

* **masque** ce champ sur la fiche d'un particulier, où seul reste le nom légal
  protégé de ``bf_preferred_name`` ;
* le **renomme** « Nom légal de la société » sur la fiche d'une société, pour
  qu'on ne confonde pas les deux ;
* le **ferme** sur la fiche d'une personne : une valeur y est refusée, par
  quelque chemin qu'elle arrive (API, import), sauf sur la fiche d'une
  société Odoo, dont l'en-tête des documents la lit (« Type de société »
  compte comme la case « société ») ;
* à son installation, **déplace** vers le nom légal protégé les valeurs déjà
  tapées sur des personnes, sans trace au fil, et sans jamais écraser un nom
  légal protégé déjà posé.

Il ne touche pas aux noms légaux des sociétés ni à l'en-tête des documents.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_preferred_name", "partner_legal_name"],
    "data": [
        "views/res_partner_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "auto_install": True,
    "application": False,
}
