# -*- coding: utf-8 -*-
{
    "name": "Menu des applications cherchable",
    # 18.0.1.1.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    # 18.0.1.2.0: le téléphone aussi. Odoo ne monte le panneau que sur poste de
    #   travail ; sur mobile il rend son tiroir (AppsMenu.Sidebar), dont la
    #   liste « Toutes les applications » n'avait aucune recherche. Le champ y
    #   vit dans un <ul> à lui, dans la zone défilante : il hérite ainsi des
    #   couleurs du tiroir (blanc sur un Odoo nu, sombre sous un habillage)
    #   sans ajouter de <li> au <ul> des applications, dont la position des
    #   lignes sert de repère aux essais d'Odoo.
    "version": "18.0.1.2.0",
    "category": "Productivity",
    "summary": "Chercher une application : une grille d'icônes au poste de travail ; "
               "sur téléphone, la liste native d'Odoo, filtrée",
    "description": """
Odoo rend le menu des applications comme une liste de noms, sans icône et sans
recherche. Passé une vingtaine d'applications, la liste dépasse l'écran et il
faut la parcourir des yeux à chaque fois.

Ce module la remplace par un panneau :

* une **grille d'icônes** avec le nom, en deux colonnes ou plus selon la largeur ;
* **défilante**, dans un panneau borné (40% de la largeur, 55% de la hauteur) ;
* un **champ de recherche** qui a déjà le curseur à l'ouverture — on clique, on
  tape, et **Entrée** ouvre la première correspondance ;
* la recherche se **vide au choix d'une tuile**, donc la fois suivante le menu
  est complet et le champ est net.

La recherche est tolérante aux fautes : elle passe par le même `fuzzyLookup`
que la palette de commandes d'Odoo, et classe par pertinence.

**Sur téléphone**, Odoo ne monte pas ce panneau : il ouvre son tiroir latéral,
dont la liste « Toutes les applications » se parcourait à l'œil. Le même champ
y est posé, au-dessus de la liste et épinglé pendant qu'on défile :

* le **curseur** s'y pose quand on touche « Toutes les applications », et là
  seulement : à la simple ouverture du tiroir, on vient chercher un menu de
  l'application en cours, et un clavier mangerait l'écran pour rien ;
* la touche « aller » du clavier ouvre la première correspondance — et ne fait
  rien sur un champ vide ;
* le champ se vide à chaque sortie du tiroir, rotation de l'écran comprise, pour
  qu'il ne se rouvre jamais sur une liste filtrée qu'on lirait comme une liste
  amputée.

La forme des lignes du tiroir ne change pas : une application par ligne, grande
icône, cible large au doigt.

Rien d'autre ne change. Au poste de travail, les entrées restent des
``DropdownItem`` : mêmes liens, même navigation au clavier, même classe
``o_app``. Dans le tiroir, le champ vit dans un ``<ul>`` à lui, frère de celui
des applications : les sélecteurs de position d'Odoo
(``li:nth-of-type(n)``, ``.o_burger_menu_app > ul > li:nth-of-type(2)``)
désignent donc les mêmes applications qu'avant — mesuré dans le DOM du tiroir.

Au poste de travail, les couleurs suivent les variables ``--brand-*`` si
l'instance en pose, et retombent sinon sur des valeurs neutres. Dans le tiroir,
le module ne nomme aucune couleur : tout vient par héritage, et la pastille du
champ a un repli pour les navigateurs sans ``color-mix``.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["web"],
    "assets": {
        "web.assets_backend": [
            "bf_apps_menu/static/src/scss/apps_menu.scss",
            "bf_apps_menu/static/src/xml/apps_menu.xml",
            "bf_apps_menu/static/src/js/apps_menu.js",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
