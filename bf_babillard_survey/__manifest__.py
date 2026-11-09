# -*- coding: utf-8 -*-
{
    "name": "Babillard : sondages Odoo",
    "summary": "Annoncer un sondage Odoo au fil, avec son lien ; et sa séance en direct, avec son code",
    "version": "18.0.1.1.0",
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

Et la séance en direct, avec son code
=====================================

Un second bouton, qui ne paraît que lorsqu'une séance est OUVERTE : la carte
porte alors le code de la séance et le lien court pour rejoindre. Sans lui, rien
n'annonce une séance ouverte à la maison, et l'animateur écrit le code dans un
message ou personne n'arrive.

⚠️ Le babillard ne garde qu'une carte par source. La deuxième séance d'un même
sondage réécrit donc la carte avec le code du jour et la remet en tête du fil :
une carte qui garderait le code du mois dernier serait pire qu'absente. La
republication remet aussi l'échéance à vide : sans ça, une carte échue le mois
dernier et republiée ce matin retombait du fil au prochain passage du cron qui
fait échoir les publications — pendant la séance.

⚠️ Et une seule carte par sondage, toutes sociétés confondues : si la carte
d'un sondage est déjà au babillard d'une AUTRE société, le bouton refuse et le
dit, au lieu de déplacer cette carte-là. Un sondage Odoo ne porte aucune
société, le fil en porte une.

⚠️ Deux refus de plus, qui sont ceux du cœur et non les nôtres : un code de
séance ne mène nulle part sur une **certification**, ni sur un sondage réservé
aux personnes **invitées**.
""",
}
