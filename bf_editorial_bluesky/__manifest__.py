# -*- coding: utf-8 -*-
{
    "name": "Atelier éditorial — Bluesky",
    "version": "18.0.1.0.0",
    "category": "Marketing",
    "summary": "Diffuser sur Bluesky depuis l'atelier éditorial, par le"
               " protocole AT et un mot de passe d'application",
    "description": """
Atelier éditorial — Bluesky
===========================

Le connecteur Bluesky du cadre de diffusion. Il implémente le contrat
``bf.social.connector`` et rien d'autre : aucune logique de file, aucune
règle éditoriale.

Pourquoi Bluesky en premier
---------------------------
C'est le réseau qui demande le moins : un mot de passe d'application créé
depuis les réglages du compte, aucune candidature d'API, aucune revue
d'application, aucun palier payant. Il valide donc l'interface abstraite à
moindre risque avant les réseaux plus exigeants.

Ce que le connecteur fait
-------------------------
* Ouvre une session par mot de passe d'application, jamais avec le mot de
  passe du compte.
* Publie le texte avec sa **carte de lien** : le titre, la description et la
  vignette de l'article sont joints, sinon le lien s'affiche nu.
* Découpe correctement les **liens et les mots-clics** : le protocole AT
  attend des positions en octets UTF-8, pas en caractères. Un accent mal
  compté décale tout le reste de la ligne.
* Rapatrie mentions j'aime, repartages et réponses.

Limites assumées
----------------
* Bluesky ne publie pas de nombre d'affichages : la mesure d'impressions
  reste vide, et c'est le réseau qui décide, pas le module.
* Le texte est limité à 300 caractères, comptés en graphèmes par le réseau.
  Le module applique la limite à la saisie.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_editorial_social"],
    "external_dependencies": {"python": ["requests"]},
    "data": [],
    "application": False,
    "installable": True,
    "auto_install": False,
}
