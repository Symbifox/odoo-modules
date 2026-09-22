# -*- coding: utf-8 -*-
{
    "name": "Chronomètre de rencontre",
    "version": "18.0.1.4.0",
    "category": "Services/Meetings",
    "summary": "Le temps réel passé sur chaque sujet, et l'heure de fin projetée "
               "pendant que la rencontre dure encore",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": False,
    "depends": ["bf_meeting"],
    "description": """
Chronomètre de rencontre
========================

Un ordre du jour porte déjà des sujets et des minutes allouées. Personne ne sait
pour autant où passent ces minutes : la somme des sujets est bâtie pour remplir
la case du calendrier, et le compte rendu ne dit jamais que le premier sujet en
a mangé la moitié.

Ce module chronomètre la rencontre pendant qu'elle a lieu, sujet par sujet, et
laisse derrière lui le temps réel de chacun.

Ce qu'il montre pendant la rencontre
------------------------------------
* **L'heure de fin projetée**, contre l'heure de fin prévue. C'est le seul
  chiffre qui change une décision pendant qu'il est encore temps.
* L'écart cumulé contre le plan, sujet par sujet.
* Ce que le sujet qu'on vient de quitter a coûté.
* Ce qu'il reste d'alloué aux sujets pas encore abordés.

Les gestes
----------
Démarrer, sujet suivant, revenir à un sujet, passer un sujet, « + Varia »,
pause, terminer. Un sujet imprévu qui naît en cours de rencontre est
chronométré comme les autres, et un sujet repris plus tard cumule ses passages.
Revenir aussitôt au sujet qu'on vient de quitter annule le « Sujet suivant ».

Les notes par sujet
-------------------
Les notes en direct se prennent sujet par sujet, à côté de la liste des
sujets, et vont aux points du compte rendu. Le fil continu reste disponible
par un réglage de société.

Ce qu'il laisse
---------------
Le temps réel de chaque sujet reste sur l'ordre du jour, et le compte rendu
l'imprime. C'est la matière première du seul calibrage honnête : combien de
temps un sujet prend vraiment.

Ce qu'il n'est pas
------------------
Ce n'est pas un chronomètre de speedrun. Les sujets d'une rencontre ne se
répètent pas d'une instance à l'autre : il n'y a donc ni meilleur segment, ni
somme des meilleurs, ni record à battre. La seule comparaison est le plan.
""",
    "data": [
        "views/res_company_views.xml",
        "views/meeting_agenda_views.xml",
        "report/meeting_report_templates.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_meeting_timer/static/src/js/meeting_timer_store.js",
            "bf_meeting_timer/static/src/js/meeting_timer_panel.js",
            "bf_meeting_timer/static/src/js/meeting_timer_notes.js",
            "bf_meeting_timer/static/src/xml/meeting_timer_panel.xml",
            "bf_meeting_timer/static/src/xml/meeting_timer_notes.xml",
            "bf_meeting_timer/static/src/scss/meeting_timer_panel.scss",
        ],
    },
}
