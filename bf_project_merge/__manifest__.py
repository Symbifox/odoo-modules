{
    "name": "Symbifox — Regroupement de tâches",
    "version": "18.0.1.0.0",
    "category": "Services/Project",
    "summary": "Regrouper des tâches en réattribuant leur contenu (conversation, "
    "activités, heures, dépendances) vers la tâche conservée, puis archiver le reste.",
    "description": """
Symbifox — Regroupement de tâches
=================================

Un assistant « Regrouper les tâches » qui **déplace** le contenu réel des tâches
choisies — messages, courriels et notes du chatter, activités planifiées,
suiveurs, pièces jointes, évaluations, événements de calendrier, feuilles de
temps et dépendances — vers la tâche conservée, avant d'archiver les autres.

Contrairement à un simple archivage, rien n'est laissé en arrière sur la tâche
archivée : la conversation et le travail sont consolidés là où on les cherchera.

Les réattributions hors conversation de base (heures, dépendances, évaluations,
calendrier) sont protégées par détection de champ/modèle : le module reste
installable même si la fonction concernée n'est pas active.

Antériorité : ce module remplit le même besoin que le module communautaire OCA
``project_merge`` (Onestein), mais il est écrit de façon indépendante par Blue
Fox Inc. et ajoute la réattribution complète du contenu (chatter, heures,
dépendances, etc.).
""",
    "author": "Les services de consultation Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["project"],
    "data": [
        "security/ir.model.access.csv",
        "wizard/task_merge_wizard_views.xml",
    ],
    "installable": True,
    "application": False,
}
