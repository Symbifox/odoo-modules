# -*- coding: utf-8 -*-
{
    "name": "Symbifox Noms d'usage et pronoms — pont Persona",
    # 18.0.1.0.0: premier jet. Le bandeau du
    #   composeur de bf_persona dit, pour chaque destinataire en À et en Cc, les
    #   pronoms et le genre grammatical qu'il a donnés, avant qu'on lui écrive.
    "version": "18.0.1.0.0",
    "category": "Productivity/Discuss",
    "summary": "Les pronoms des destinataires dans le bandeau du composeur",
    "description": """
Pont Persona
============

Le bandeau du composeur de ``bf_persona`` rappelle, avant d'écrire, comment un
contact aime qu'on lui écrive. Ce pont, installé de lui-même quand les deux
modules sont là, y ajoute les **pronoms et le genre grammatical** de chaque
destinataire en À et en Cc qui les a donnés, qu'il ait un persona ou non.

Le bouton « Appliquer le persona » ne paraît que s'il y a un persona à
appliquer. Rien n'est dit de quelqu'un qui n'a rien donné. Le texte proposé par Gen, lui,
suit la consigne d'écriture de chaque personne, épicène quand elle est vide.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_preferred_name", "bf_persona"],
    "data": [
        "views/mail_compose_message_views.xml",
    ],
    "installable": True,
    "auto_install": True,
    "application": False,
}
