# -*- coding: utf-8 -*-
{
    "name": "Flux RSS : bandeau de la maison",
    "summary": "Le résumé des flux porte la mise en page de la maison, avec son "
               "propre titre au bandeau au lieu du nom de la société",
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
    "depends": ["bf_flux", "bluefox_branding"],
    "data": ["views/mail_layout.xml"],
    "description": """
Flux RSS : bandeau de la maison
===============================

Le résumé courriel des flux prend la mise en page de la maison
(``bluefox_branding``), mais son bandeau dit « Vos flux de nouvelles » là où les
autres courriels portent le nom de la société : on reconnaît le résumé avant
de l'ouvrir.

La variante hérite de la mise en page commune et n'en remplace que ce titre :
tout ce qui change dans la mise en page de la maison (logo, couleurs, pied) la
suit. Les autres courriels ne sont pas touchés, et la mise en page commune n'a
pas à être remontée.
""",
}
