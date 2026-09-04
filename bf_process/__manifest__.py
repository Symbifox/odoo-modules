# -*- coding: utf-8 -*-
{
    "name": "Cartographie de processus",
    "version": "18.0.5.0.1",
    "category": "Services/Project",
    "summary": "Cartographies BPMN vivantes : le modèle est la vérité, le PDF et"
               " les deux XML n'en sont que des rendus",
    "description": """
Cartographie de processus
=========================

Une cartographie AS-IS cesse d'être un livrable ponctuel pour devenir un
référentiel qu'on travaille en continu. Le modèle sémantique — pools, couloirs,
nœuds, flux — vit en enregistrements Odoo. Les fichiers en sortent.

Fonctionnalités
---------------
* Processus versionné, rattaché à un client et à un projet, avec ses niveaux :
  niveau 1 en vue d'ensemble, sous-processus repliés en niveaux 2 et 3.
* Nœuds sur discussion : chaque étape porte ses pièces jointes (photos
  d'atelier, vidéos, documents), son fil et son historique. C'est ce qui fait
  vivre la donnée entre deux séances de cartographie.
* Identifiants BPMN stables : un fichier retouché ailleurs **revient dans la
  carte d'origine** au lieu d'en créer une copie. Les niveaux et les nœuds se
  reconnaissent à leur identifiant, jamais à leur nom, si bien qu'un
  renommage se lit comme un renommage. Chaque écart s'arbitre un par un, et
  les retraits ne s'appliquent pas tout seuls : un éditeur qui ne réexporte
  que la page ouverte produit un fichier où toutes les autres « manquent ».
* Éditeur intégré : le niveau se lit ET se travaille directement dans Odoo,
  tracé en SVG depuis les enregistrements. Glisser une forme la recale sur la
  grille du modèle ; poser, lier et retirer écrivent dans les enregistrements.
  Aucune bibliothèque tierce, donc aucun filigrane imposé et aucun appel
  sortant.
* **Le livrable, pas seulement le tracé** : couverture, légende dessinée avec
  les mêmes primitives que la carte, sommaire, un niveau par page, annexes
  (hypothèses, questions ouvertes, constats), registre de validation et carte
  dépliée. La prose vit en base, sur la carte : elle se gèle avec sa version.
* **Tracé PDF côté serveur**, à l'échelle 1:1, sans navigateur ni moteur
  typographique : la largeur de la police est étalonnée une fois et figée, et
  le PDF sort de la même géométrie que les deux exports XML.
* Export **mxGraph** (`.drawio`) pour **diagrams.net**, libre sous Apache-2.0 :
  c'est la cible de sortie retenue en priorité. Export **BPMN 2.0** (`.bpmn`
  avec sa partie DI) pour les éditeurs BPMN au sens large ; Lucidchart, qui
  est propriétaire, l'importe aussi mais vient au second rang.
* Mise en page calculée, avec surcharge par nœud : un ajustement humain
  survit à la régénération.
* **L'état actuel et le processus souhaité, et l'écart entre les deux.** Une
  cible se dessine d'après une version de l'état actuel, dans une branche à
  elle : l'état actuel peut être recartographié sans que la cible se détache.
  Les écarts se sèment depuis la même comparaison que l'assistant, puis chacun
  reçoit ce qu'aucun calcul ne peut deviner : l'intention, le gain attendu,
  l'effort, un responsable, un état et la tâche qui fait le travail. Resemer
  reconnaît les écarts déjà consignés au lieu d'en créer des jumeaux, et
  n'écrase jamais ce qu'une personne a écrit. Le delta se lit sur deux cartes
  teintées, à l'écran comme au PDF, et le livrable de la cible porte son plan
  de transformation.

Limites assumées
----------------
* Le retour n'existe qu'en BPMN 2.0, et seuls les éditeurs qui en réexportent
  peuvent le nourrir. Ni diagrams.net ni Lucidchart n'en réexporte : ce qu'on
  y redessine ne revient pas. La base reste ici, et c'est l'éditeur intégré
  qui la travaille.
* Un fichier BPMN ne porte ni le ton d'une annotation, ni le lien entre un
  sous-processus et sa page. La fusion conserve donc ce qui est déjà en place
  plutôt que de le redéduire, et nomme les sous-processus restés sans page.
* La hauteur d'un couloir est celle de son contenu, et son premier nœud est
  collé à sa marge haute. Déplacer verticalement le nœud le plus haut d'un
  couloir ne le fait donc pas descendre : ce sont les autres qui remontent.
  C'est la géométrie du générateur, et l'éditeur la montre telle quelle.
* La mesure du texte couvre les caractères que Lexend porte. Un caractère hors
  table — un émoji, par exemple — fait refuser la mesure plutôt que deviner.
* La vue delta trace deux cartes plutôt qu'une seule carte fusionnée : une
  étape retirée n'a pas de case dans la grille de la cible, et lui en inventer
  une ferait entrer en collision des cases que le modèle a placées.
* Sur une carte de l'état actuel, les teintes ne s'affichent que s'il n'existe
  qu'un seul processus souhaité. Avec deux, choisir en silence ferait lire un
  plan qui n'est pas celui qu'on regarde.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["mail", "project", "contacts", "portal",
                "project_knowledge_matrix"],
    "data": [
        "security/bf_process_security.xml",
        "security/ir.model.access.csv",
        "views/bf_process_views.xml",
        "views/bf_process_diagram_views.xml",
        "views/bf_process_node_views.xml",
        "views/bf_process_resource_views.xml",
        "views/bf_process_portal_templates.xml",
        "views/bf_process_document_views.xml",
        "views/bf_process_ecart_views.xml",
        "views/bf_process_wizard_views.xml",
        "views/menu_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_process/static/src/visualiseur/visualiseur.js",
            "bf_process/static/src/visualiseur/visualiseur.xml",
            "bf_process/static/src/visualiseur/visualiseur.scss",
        ],
    },
    "application": True,
    "installable": True,
    "auto_install": False,
}
