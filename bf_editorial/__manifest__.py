# -*- coding: utf-8 -*-
{
    "name": "Atelier éditorial",
    "version": "18.0.1.7.0",
    "category": "Marketing",
    "summary": "Calendrier éditorial, cadence mesurée et contrôles de publication"
               " pour le blogue — l'état se calcule, seules les décisions se stockent",
    "description": """
Atelier éditorial
=================

Odoo Community sait si un billet est publié ou non. Il ne sait rien d'autre :
ni le pilier, ni la cadence, ni ce qui bloque quoi, ni si l'anglais suit. Ce
module ajoute la couche qui manque, et il la calcule au lieu de la recopier.

Le principe de conception tient en une phrase : **ne stocker que les décisions,
dériver tout le reste**. Un calendrier éditorial tenu à la main dérive parce
qu'il recopie des faits (dernière publication, nombre de visites, nombre de
mots) que la base connaît déjà. Ici, ces faits sont des champs calculés.

Fonctionnalités
---------------
* **Entrée éditoriale** rattachée ou non à un billet : angle, promesse,
  audience, mot-clé visé, dépendances de publication, sources et affirmations
  vérifiées, liste de contrôle des restes humains.
* **Créneaux de langue** : une entrée porte une version par langue, avec son
  état, ses mots et son slug figé. Une entrée reste incomplète tant que toutes
  ses langues requises ne sont pas sorties, et ce comportement se règle.
* **Proposition du prochain article** : cadence, ratio par pilier, dépendances
  et blocages sont évalués en base, pas dans une note. La recommandation
  s'explique, ligne par ligne.
* **Contrôles déterministes** sans intelligence artificielle : tiret cadratin,
  formules bannies en français et en anglais, titres vides, `th` sans portée,
  images sans texte alternatif, couleurs en ligne à faible contraste,
  marqueurs de rédaction oubliés, plancher de mots, cohérence de structure
  entre créneaux de langue.
* **Garde de pré-vol** : la publication refuse tant que la liste de contrôle
  n'est pas verte, que la publication soit manuelle ou différée.
* **Dérive de version** : un billet qui documente un module signale de lui-même
  que le module a bougé depuis le dernier fact-check.
* **Piliers et sujets** repris de la taxonomie native du blogue, donc visibles
  et filtrables sur le site public sans travail supplémentaire.

Limites assumées
----------------
* Le module tient l'état et les contrôles mécaniques. Il ne juge pas un angle,
  ne décide pas qu'un sujet est mort, et ne remplace pas une relecture.
* Les contrôles de langue portent sur la structure et le vocabulaire, jamais
  sur la qualité d'une traduction.
* Rien n'est écrit dans le contenu d'un billet : le module lit, mesure et
  refuse, il ne réécrit pas.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": [
        "mail",
        "website_blog",
        "utm",
        "link_tracker",
        "project",
    ],
    "data": [
        "security/bf_editorial_security.xml",
        "security/ir.model.access.csv",
        "data/bf_editorial_data.xml",
        "views/bf_editorial_calendar_views.xml",
        "views/bf_editorial_entry_views.xml",
        "views/bf_editorial_satellite_views.xml",
        "views/blog_views.xml",
        "views/res_config_settings_views.xml",
        "views/menu_views.xml",
    ],
    "application": True,
    "installable": True,
    "auto_install": False,
}
