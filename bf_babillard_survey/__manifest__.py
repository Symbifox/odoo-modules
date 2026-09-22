# -*- coding: utf-8 -*-
{
    "name": "Babillard : sondages Odoo",
    "summary": "Annoncer un sondage Odoo au fil, avec son lien",
    "version": "18.0.1.0.0",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_babillard", "survey"],
    "data": ["views/survey_views.xml"],
    "auto_install": True,
    "installable": True,
    "description": """
Pont Babillard ↔ Sondages
=========================

S'auto-installe quand `bf_babillard` et Sondages sont tous deux là.

Le babillard porte ses propres sondages : une question, des choix, un clic. Ce
pont sert l'autre besoin, celui du questionnaire : un sondage Odoo a des pages,
des sections, des questions notées, et il se répond ailleurs. Le fil l'annonce
et donne son lien, il ne le rejoue pas.

C'est un geste de la rédaction, pas un automatisme : un questionnaire ouvert
n'est pas forcément une nouvelle de la maison, et le fil est le seul endroit que
tout le monde lit.

⚠️ Un sondage réservé aux invités ne s'annonce pas. Son lien de départ exige
une invitation nominative : posé sur une carte lue par toute la maison, il ne
mène personne nulle part.
""",
}
