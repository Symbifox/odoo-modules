# -*- coding: utf-8 -*-
{
    "name": "Atelier éditorial — Gen",
    "version": "18.0.1.4.1",
    "category": "Marketing",
    "summary": "Suggestion, revue et étoffement d'articles par Gen,"
               " en propositions relues avant d'être appliquées",
    "description": """
Atelier éditorial — Gen
==========================

`bf_editorial` calcule et refuse ; il ne juge pas et il ne réécrit pas. Ce
module lui adjoint le jugement, sans lui retirer sa réserve : **Gen propose,
un humain applique.**

Trois gestes
------------
* **Suggérer le prochain article**, à la racine du module. Le classement
  déterministe dit ce qui est le plus avancé ; Gen dit si l'angle tient
  encore, si le pilier en retard a seulement un candidat, et ce qu'il ferait.
* **Revue Gen** sur une entrée : la moitié que les expressions régulières
  n'atteignent pas — répétitions, texte des liens, dérive par rapport à l'angle
  déclaré, fidélité de la traduction, style maison.
* **Étoffer et aligner** : un texte proposé, rangé à côté de l'article et
  jamais posé à sa place. L'appliquer est un second geste, et il refuse si
  l'article a bougé depuis que la proposition a été calculée.

Ce que le module ne fait pas
----------------------------
* Il n'écrit jamais de lui-même dans un billet. `action_apply` est manuelle,
  tracée, et gardée par une empreinte du texte d'origine.
* Il ne remplace pas la QA déterministe de `bf_editorial` : il la complète.
  Une revue Gen ne rend pas la garde de pré-vol verte.

Dépendance à Gen
-------------------
Les boutons n'apparaissent que si la socket du pont répond
(`bf.ai.bridge.available()`). Sans Gen, le module s'installe et se tait.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": [
        "bf_editorial",
        "bf_ai_bridge",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/bf_editorial_suggestion_views.xml",
        "views/bf_editorial_entry_views.xml",
        "views/bf_editorial_calendar_views.xml",
        "views/menu_views.xml",
    ],
    "application": False,
    "installable": True,
    "auto_install": False,
}
