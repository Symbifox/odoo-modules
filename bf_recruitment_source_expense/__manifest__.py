{
    "name": "Recrutement : ce que chaque site d'emploi coûte",
    "summary": "Imputer un débours d'affichage au site qui l'a facturé, et "
               "rendre le coût par candidature, en disant quelle part des "
               "débours du poste il couvre",
    "version": "18.0.1.0.0",
    "category": "Human Resources/Recruitment",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    # Le troisième côté. Les statistiques par source vivent sans les dépenses,
    # les dépenses vivent sans les statistiques, et le coût par candidature
    # n'existe que quand les deux sont là.
    "auto_install": True,
    "description": """
Recrutement : ce que chaque site d'emploi coûte
================================================

Deux modules se croisent ici. `bf_recruitment_expense` sait ce qu'un poste a coûté ;
`bf_recruitment_source` sait ce que chaque site a rapporté. Ni l'un ni l'autre ne sait ce
qu'un site a coûté, parce que la dépense s'arrêtait au poste.

Ce que ça ajoute
----------------

* **Un site sur le débours** (`hr.expense.recruitment_source_id`). On ne paie
  pas « un poste », on paie SEEK pour un affichage sur un poste. Choisir le
  site remplit le poste tout seul.
* **Le coût par candidature** et **le coût par embauche**, par site.
* **Ce que les sites n'expliquent pas** : les débours rattachés au poste sans
  l'être à un site.

La propriété qui fait le pont
------------------------------

🔴 **Un coût par candidature qui compte tout le poste ment vers le haut ; un
qui ne compte que ce qui lui est imputé ment vers le bas s'il se tait.** Le
module prend le second chemin et ne se tait pas :

* `expense_total` d'une source ne compte QUE les débours imputés à cette
  source ;
* `unattributed_expense_total` du poste compte ceux qui n'ont pas de site ;
* l'avertissement du poste écrit la somme non imputée, en argent, à côté du
  nombre de candidatures que personne n'explique.

⚠️ Comme dans `bf_recruitment_expense`, une dépense **refusée** n'est pas un débours, et le coût
par candidature ne compte pas le temps du panel : celui-là appartient au poste
et non au site qui a porté l'annonce. Un site ne fait pas passer d'entrevues.
""",
    "depends": [
        "bf_recruitment_source",
        "bf_recruitment_expense",
    ],
    "data": [
        "views/hr_expense_views.xml",
        "views/hr_job_views.xml",
    ],
}
