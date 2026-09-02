# -*- coding: utf-8 -*-
{
    "name": "Atelier éditorial — réseaux sociaux",
    "version": "18.0.1.5.0",
    "category": "Marketing",
    "summary": "Diffusion différée sur les réseaux sociaux depuis l'atelier"
               " éditorial, avec garantie de non-doublon et retour de mesure",
    "description": """
Atelier éditorial — réseaux sociaux
===================================

Le cadre de diffusion. Il ne parle à aucun réseau en particulier : chaque
réseau arrive dans son propre module, qui implémente la même interface.

Ce que ce module apporte
------------------------
* **Un canal par compte** : le réseau, le pseudonyme, la langue publiée, et
  les identifiants chiffrés hors de la base.
* **Une file de diffusion différée** : un billet part à l'heure dite, ou pas
  du tout si la garde de pré-vol de son article refuse encore.
* **La garantie de non-doublon.** C'est le point le plus important du module.
  Un travail périodique qui reprend une file après une coupure réseau
  republie, si rien ne l'en empêche. Ici, chaque billet porte une clé unique
  par canal, la file le réserve dans sa propre transaction avant tout appel
  sortant, et l'identifiant distant est écrit dès la réponse.
* **Le retour de mesure** : mentions j'aime, repartages, réponses et clics
  reviennent sur l'entrée éditoriale, en série datée plutôt qu'en compteur.
* **Les blurbs** : un texte par article, par canal et par langue, avec la
  limite de caractères du réseau appliquée à la saisie.

Limites assumées
----------------
* Aucun réseau n'est joignable sans son module de connecteur.
* Le module ne rédige pas les blurbs et ne choisit pas les heures.
* Les identifiants ne sont jamais stockés en clair, et la clé de chiffrement
  ne vit jamais en base : elle vient de l'environnement ou du fichier de
  configuration.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_editorial", "link_tracker", "utm"],
    "external_dependencies": {"python": ["cryptography"]},
    "data": [
        "security/bf_editorial_social_security.xml",
        "security/ir.model.access.csv",
        "data/bf_editorial_social_data.xml",
        "views/bf_social_channel_views.xml",
        "views/bf_social_post_views.xml",
        "views/bf_editorial_entry_views.xml",
        "views/menu_views.xml",
    ],
    "application": False,
    "installable": True,
    "auto_install": False,
}
