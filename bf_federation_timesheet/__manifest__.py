{
    "name": "Fédération : relevés des heures",
    "version": "18.0.1.0.0",
    "category": "Project",
    "summary": "Le relevé des heures d'une période, remis au partenaire comme un livrable, avec son accusé de réception",
    "description": """
Fédération : relevés des heures
===============================

Entre un sous-traitant et un entrepreneur général, les heures faites sont l'une
des choses les plus évidemment utiles à partager. Mais ce qu'un partenaire veut
recevoir, ce n'est pas la ligne de temps qui change tous les jours : c'est **le
relevé**, arrêté à une date, pour une période, et dont on sait qu'il a été lu.

Ce module ne fait donc traverser aucune ligne de temps. Il produit un relevé et
le remet comme un livrable fédéré.

* **Un relevé par période.** Le projet, du premier au dernier jour, regroupé par
  tâche, par personne ou par jour, avec ou sans les descriptions. Un PDF pour le
  lire, un CSV pour le reprendre.
* **L'accusé de réception du livrable.** Le partenaire dit qu'il l'a lu, et qui.
* **Un relevé corrigé périme l'accusé.** Refaire le relevé d'une période déjà
  remise en publie une nouvelle version : l'accusé de la précédente tombe des deux
  côtés, parce qu'un accusé porte sur un contenu.
* **Ce qui ne traverse jamais** : les montants, les taux, le coût, le solde d'une
  banque d'heures. Un relevé dit combien de temps, pas combien d'argent.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_federation_document", "hr_timesheet"],
    "data": [
        "security/ir.model.access.csv",
        "report/timesheet_statement_report.xml",
        "wizard/timesheet_statement_views.xml",
        "views/project_project_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
