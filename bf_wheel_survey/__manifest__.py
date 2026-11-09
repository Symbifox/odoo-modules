# -*- coding: utf-8 -*-
{
    "name": "Roue de fortune : les participants d'une séance",
    "version": "18.0.1.1.0",
    "category": "Marketing/Surveys",
    "summary": "La roue tire parmi les gens qui ont rejoint la séance en direct, "
               "ou parmi les réponses d'une question",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["bf_wheel", "survey"],
    "auto_install": True,
    "installable": True,
    "data": [
        "views/bf_wheel_views.xml",
        "views/survey_views.xml",
    ],
    "description": """
Roue de fortune ↔ Sondages
==========================

S'auto-installe quand la Roue de fortune et Sondages sont tous deux là.

Odoo tient déjà la séance en direct : un code, un code QR projeté, les gens
rejoignent par `/s/<code>` avec un pseudonyme, la minuterie, le bonus de
rapidité, l'histogramme et le classement. Ce qu'il n'a pas, c'est le tirage.

Ce pont donne deux garnitures de plus à la roue :

* **Les participants de la séance en direct.** La roue tire parmi les gens qui
  ont rejoint la séance en cours, chacun avec le pseudonyme qu'il a donné. Un
  participant, une part : la chance ne dépend ni du score ni de l'heure d'arrivée.
* **Les réponses d'une question.** Une part par réponse donnée, et, au choix,
  une part d'autant plus large que la réponse a été donnée souvent : la roue
  devient alors la carte de ce que la salle a répondu.

⚠️ Une séance qui n'a pas commencé n'a pas de participants : la roue le dit au
lieu de tourner à vide. Et les participants se relisent à CHAQUE tour, donc
quelqu'un qui rejoint entre deux tirages entre dans le second.

⚠️ Un pseudonyme est un renseignement personnel dès qu'il nomme quelqu'un. Le
registre de la roue garde la liste des candidats d'un tirage, et l'élague au
terme du délai de conservation de la roue, comme les coordonnées.
""",
}
