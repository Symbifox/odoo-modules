# -*- coding: utf-8 -*-
{
    "name": "Plans d'étage",
    "summary": "Le plan du bureau dessiné depuis les enregistrements : salles, "
               "postes, appareils et câbles, avec la fiche derrière chaque forme",
    "version": "18.0.1.1.0",
    "category": "Services",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    # ⚠️ « Other proprietary » est la valeur que le schéma de manifeste
    # d'Odoo offre pour dire BUSL-1.1 : il n'a pas de valeur BUSL. C'est
    # le fichier LICENSE qui gouverne, pas cette ligne.
    "license": "Other proprietary",
    "application": True,
    "installable": True,
    "description": """
Plans d'étage
=============

Le plan d'un étage cesse d'être une image rangée quelque part pour devenir
un référentiel : chaque salle, chaque poste, chaque appareil posé sur le plan
est un enregistrement, avec sa fiche, son fil de discussion et ses photos.
L'image d'architecte reste ce qu'elle est, un fond de plan ; ce qui vit
dessus est en base.

Fonctionnalités
---------------
* Un plan par étage, aux dimensions réelles (en centimètres), sur un fond
  PNG ou JPEG facultatif : un plan se travaille aussi sur fond blanc.
* Des zones (bureaux fermés, salles de réunion, aires ouvertes, locaux
  techniques…) avec leur capacité et leur occupation calculée.
* Des éléments posés : postes de travail, tables, imprimantes, écrans,
  commutateurs, bornes Wi-Fi, serveurs, baies, prises réseau, téléphones,
  caméras. Chaque élément sait dans quelle zone il se trouve, sans qu'on le
  lui dise, et qui l'occupe.
* Des liens entre éléments (câble réseau, fibre, électrique) : le début d'un
  schéma de câblage, tracé au même endroit que le plan.
* Un éditeur intégré : le plan se lit et se travaille dans Odoo, tracé en SVG
  depuis les enregistrements. Glisser une forme la recale sur la grille ;
  poser, redimensionner, tourner, lier et retirer écrivent dans les
  enregistrements. Aucune bibliothèque tierce.
* Un plan figé ne se modifie plus, jusqu'à ce qu'on le rouvre.
* Impression PDF (lettre paysage) avec la légende et l'inventaire par zone ;
  export SVG depuis l'écran ; export **diagrams.net** (`.drawio`) avec les
  formes de sa bibliothèque « Floorplans » et un calque par nature, pour
  finir le dessin (murs, portes, fenêtres) là où c'est le mieux outillé.
* Depuis la fiche d'un employé : le bouton « Sur le plan » mène à son poste.

Le module « Plans d'étage : hébergement » relie chaque élément à un appareil
ou un serveur de la gestion d'hébergement : la fiche s'ouvre depuis le plan,
et le plan colore ce qui demande attention (système en fin de vie, garantie
échue, appareil en réparation ou retiré).

Limites assumées
----------------
* Les murs, portes et fenêtres ne se dessinent pas ici : ils viennent du
  fond de plan, ou se finissent dans diagrams.net après export. Le module
  porte ce qui a une fiche, pas la maçonnerie.
* Le fond est une image matricielle. Un SVG d'architecte se convertit en
  PNG avant d'être posé : servir un SVG tiers tel quel serait servir son
  script avec.
* Les zones et les éléments sont des rectangles, tournés par quart de tour.
  Une salle en L se pose en deux zones.
""",
    "depends": ["mail", "hr", "web"],
    "data": [
        "security/bf_floorplan_security.xml",
        "security/ir.model.access.csv",
        "views/bf_floorplan_views.xml",
        "views/bf_floorplan_zone_views.xml",
        "views/bf_floorplan_element_views.xml",
        "views/bf_floorplan_lien_views.xml",
        "views/hr_employee_views.xml",
        "report/bf_floorplan_report.xml",
        "views/menu_views.xml",
    ],
    "demo": [
        "demo/bf_floorplan_demo.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_floorplan/static/src/visualiseur/visualiseur.js",
            "bf_floorplan/static/src/visualiseur/visualiseur.xml",
            "bf_floorplan/static/src/visualiseur/visualiseur.scss",
        ],
    },
}
