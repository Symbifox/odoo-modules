# -*- coding: utf-8 -*-
{
    "name": "Célébrations : groupes de destinataires",
    "summary": "Tend une carte de fête aux groupes de destinataires du "
               "composeur de courriels",
    "version": "18.0.1.0.0",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Célébrations : groupes de destinataires
=======================================

Pont entre « Célébrations » et « Gestion des courriels ». Les groupes de
destinataires déjà entretenus pour le composeur (« Équipe de projet »,
« Direction ») deviennent une source de signataires pour une carte, à côté
des groupes de signataires du module lui-même.

Le groupe se résout **avec les droits de la personne qui invite**, jamais en
sudo, comme le composeur le fait déjà : un groupe partagé par quelqu'un
d'autre ne devient pas un moyen d'écrire à des contacts qu'on ne voit pas.
Le plafond propre aux groupes de destinataires n'est pas appliqué ici, c'est
celui des invitations de Célébrations qui borne l'envoi ; et si la fonction
des groupes est éteinte sur le locataire (`recipient_group_enabled`), le
pont ne contribue rien.

Tout le reste est celui de Célébrations : une invitation par adresse, la
personne fêtée retirée par toutes ses adresses, un compte au chatter et
jamais des noms.
""",
    "depends": ["bf_celebrations", "bf_email_management"],
    "data": [
        "views/celebration_board_views.xml",
    ],
}
