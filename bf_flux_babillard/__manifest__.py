# -*- coding: utf-8 -*-
{
    "name": "Flux RSS : babillard",
    "summary": "Un élément de flux devient un brouillon du babillard, adressé "
               "aux services de la liste qui l'a retenu",
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ « Other proprietary » est la valeur que le schéma de manifeste d'Odoo
    # offre pour dire BUSL-1.1 : il n'a pas de valeur BUSL. C'est le fichier
    # LICENSE qui gouverne, pas cette ligne.
    "license": "Other proprietary",
    "installable": True,
    "auto_install": True,
    "depends": ["bf_flux", "bf_babillard"],
    "data": ["views/flux_views.xml"],
    "description": """
Flux RSS : babillard
====================

Un bouton « Publier au babillard » sur un élément de flux retenu. Il prépare
un **brouillon** : titre, chapeau, lien, et une audience reprise des services
des listes qui ont retenu l'élément (toute la maison si aucune n'en vise).

* **Un brouillon, jamais une publication directe.** Le babillard s'adresse à
  tout le personnel : c'est la rédaction qui décide de ce qui y paraît, et le
  bouton n'est offert qu'à elle.
* **Une seule carte par élément**, même si on clique deux fois : le bouton
  rouvre le brouillon déjà préparé.
""",
}
