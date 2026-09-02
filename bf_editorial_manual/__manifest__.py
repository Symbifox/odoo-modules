# -*- coding: utf-8 -*-
{
    "name": "Atelier éditorial — canal manuel",
    "version": "18.0.1.1.1",
    "category": "Marketing",
    "summary": "Un canal qu'on alimente à la main : le texte se rédige et se"
               " relit dans Odoo, la publication se fait sur le réseau",
    "description": """
Canal manuel
============

Tous les réseaux ne se publient pas par API. LinkedIn, par exemple, réserve la
publication sur une page à son produit *Community Management API* : entité
légale enregistrée, page vérifiée, révision d'application en deux temps. Tant
que cette porte n'est pas ouverte, la publication reste un copier-coller.

Ce module ne prétend pas la contourner. Il donne au réseau un canal en bonne et
due forme — donc un blurb rédigé, relu par la QA maison, mesuré contre la vraie
limite de caractères, et rattaché à son article avec un lien suivi — puis il
s'arrête là où l'automatisation s'arrête vraiment.

Ce que ça change concrètement
-----------------------------
* Gen écrit un blurb pour ce canal comme pour n'importe quel autre.
* La QA éditoriale s'y applique : tirets cadratins, formules bannies, longueur.
* Le lien suivi est résolu, donc les clics restent attribuables même publiés
  à la main.
* La diffusion refuse, avec un message qui dit quoi faire plutôt que d'échouer
  en silence, et un bouton marque le billet comme diffusé une fois le
  copier-coller fait.

Le jour où l'API s'ouvre
------------------------
Le canal change de réseau, les blurbs déjà écrits restent. Rien de ce qui est
rédigé ici n'est perdu à la bascule.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_editorial_social"],
    "data": ["views/bf_social_post_views.xml"],
    "installable": True,
    "auto_install": False,
}
