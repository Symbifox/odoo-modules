# -*- coding: utf-8 -*-
{
    "name": "Séance en direct : le socle",
    "version": "18.0.1.1.0",
    "category": "Productivity",
    "summary": "La palette projetée, le calcul de contraste et la rampe de la marque, partagés par les écrans de séance",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "application": False,
    "installable": True,
    "auto_install": False,
    "depends": ["base"],
    "data": [],
    "description": """
Séance en direct : le socle
===========================

Ce module ne porte aucun écran, aucun modèle et aucun menu. Il porte ce que les
écrans de la famille se partagent : **une palette de douze teintes mesurées** et
**le calcul qui choisit l'encre** qu'on écrit dessus.

Pourquoi un module pour douze couleurs
--------------------------------------
Parce qu'elles ne sont pas choisies, elles sont mesurées. Chaque teinte est posée
à une luminosité où l'une des deux encres de la maison atteint le minimum de la
WCAG pour un texte normal, 4,5:1. Quatre teintes choisies d'instinct tombaient
entre les deux, trop sombres pour l'encre foncée et trop claires pour la blanche,
et portaient une étiquette illisible à 3,5:1. Un essai relit ce minimum sur les
douze, à chaque montée.

Et parce qu'elles servent à trois modules : les segments d'une roue, les équipes
d'une séance, les blocs d'un podium. Tant qu'elles vivaient dans la roue, avoir
le nuage de mots obligeait à installer la roue.

Ce qu'il expose
---------------
`odoo.addons.bf_live_base.models.color_utils` :

* `PALETTE` : les douze teintes, le bleu de la maison en tête ;
* `palette_color(rang)` : la couleur d'un rang, la palette tournant indéfiniment ;
* `text_color(fond)` : celle des deux encres qui contraste le plus avec ce fond ;
* `contrast_ratio(a, b)` : le rapport de la WCAG, de 1:1 à 21:1 ;
* `relative_luminance`, `normalize_hex`, `is_hex` ;
* `brand_ramp(couleur)` : trois marches d'un même ton, pour les blocs d'un podium.

La rampe part de la couleur donnée — celle de la société, quand elle en a une —
et descend. Une teinte presque noire fait monter la rampe au lieu de la
descendre : on ne distingue pas trois noirs. Et une marche qui tombe ENTRE les
deux encres est poussée jusqu'à ce que l'une des deux atteigne 4,5:1, au lieu
d'être remplacée en silence par une couleur de la palette.

⚠️ `text_color` compare les deux rapports au lieu de franchir un seuil. Le seuil
qui circule (luminance 0,5) suppose une encre noire ; l'encre de la maison est
l'anthracite #2d3031, dont le point d'égalité est à 0,238. À 0,5, le bleu de la
maison recevrait du blanc, qui s'y lit à 2,6:1.
""",
}
