{
    "name": "Symbifox — Ancrage de la récurrence",
    "version": "18.0.1.0.0",
    "category": "Services/Project",
    "summary": "Choisir, par série récurrente, si la prochaine échéance part de "
    "l'ancienne échéance ou de la date de fermeture réelle.",
    "description": """
Blue Fox — Ancrage de la récurrence
===================================

Le noyau ``project`` ne sait reporter une tâche récurrente que depuis son
ancienne **échéance** : fermer la tâche trois jours en avance ne rapproche pas
l'occurrence suivante, et la fermer trois semaines en retard ne la repousse pas.
Sur une série « toutes les semaines », un retard finit par empiler des
occurrences déjà en souffrance au moment de leur création.

Ce module ajoute un champ **Calculer la prochaine échéance** sur la récurrence :

* **Depuis l'échéance** — comportement du noyau, inchangé au bit près.
* **Depuis la date de fermeture** — la prochaine échéance part du moment où la
  tâche a réellement été fermée. Fermer en avance rapproche l'occurrence
  suivante ; fermer en retard la repousse d'autant, sans rattrapage.

Le choix se fait une fois par série, pas à chaque complétion.

Ce que le mode « fermeture » corrige aussi
------------------------------------------
* **Série sans échéance** : le noyau crée l'occurrence suivante sans échéance,
  et la série ne se replanifie alors plus jamais. En mode fermeture, elle en
  reçoit enfin une (fermeture + intervalle).
* **Garde ``repeat_until``** : le noyau compare l'ancienne échéance + intervalle
  à la borne de fin. En mode fermeture, la comparaison porte sur l'ancre réelle,
  sinon une série « jusqu'au » s'arrêterait au mauvais moment.
* **Sous-tâches** : le décalage est appliqué uniformément à l'arbre, donc les
  écarts relatifs entre la tâche et ses sous-tâches sont conservés.

Aucune migration : les récurrences existantes prennent ``deadline`` par défaut
et se comportent exactement comme avant.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["project"],
    "data": [
        "views/project_task_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
