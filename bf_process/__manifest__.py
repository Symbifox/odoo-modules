# -*- coding: utf-8 -*-
{
    "name": "Cartographie de processus",
    "version": "18.0.3.2.0",
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
* **Tracé PDF côté serveur**, à l'échelle 1:1, sans navigateur ni moteur
  typographique : la largeur de la police est étalonnée une fois et figée, et
  le PDF sort de la même géométrie que les deux exports XML.
* Export **mxGraph** (`.drawio`) pour **diagrams.net**, libre sous Apache-2.0 :
  c'est la cible de sortie retenue en priorité. Export **BPMN 2.0** (`.bpmn`
  avec sa partie DI) pour les éditeurs BPMN au sens large ; Lucidchart, qui
  est propriétaire, l'importe aussi mais vient au second rang.
* Mise en page calculée, avec surcharge par nœud : un ajustement humain
  survit à la régénération.

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
    """,
    "author": "Blue Fox Inc.",
    "website": "https://bluefoxconsultant.com",
    "license": "Other proprietary",
    "depends": ["mail", "project", "contacts"],
    "data": [
        "security/bf_process_security.xml",
        "security/ir.model.access.csv",
        "views/bf_process_views.xml",
        "views/bf_process_diagram_views.xml",
        "views/bf_process_node_views.xml",
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
