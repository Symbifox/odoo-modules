# -*- coding: utf-8 -*-
{
    "name": "GenFox — Cockpit",
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "summary": "Vue d'administration des sessions GenFox : pannes de flux et consommation",
    "description": """
Le cockpit d'administration de GenFox, sorti de ``bf_claude_chat`` pour que le
module de base reste ce qu'on donne à tout le monde : le clavardage.

Deux écrans, tous deux réservés à ``base.group_system`` :

* **Cockpit** — les sessions et leur compteur d'échecs de flux. Une session qui
  atteint trois échecs consécutifs cesse de reprendre son fil et en ouvre un
  neuf au message suivant. Le bouton remet le compteur à zéro une fois la cause
  comprise.
* **Consommation** — jetons et coût équivalent-API par personne et par semaine.
  Informatif, délibérément séparé de l'opérationnel.

Installer ce module ajoute les deux entrées sous le menu d'administration de
GenFox. Ne pas l'installer ne retire rien au clavardage.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_claude_chat"],
    "data": [
        "views/cockpit_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
