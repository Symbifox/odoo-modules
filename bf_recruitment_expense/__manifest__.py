{
    "name": "Recrutement : dépenses et coût par embauche",
    "summary": "Rattacher les débours de recrutement au poste, valoriser le temps "
               "de panel que le cahier d'entrevues mesure déjà, et dire ce que "
               "coûte une embauche, en avouant ce qui manque au chiffre",
    "version": "18.0.1.0.2",
    "category": "Human Resources/Recruitment",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    # Pont : il s'installe seul quand les
    # trois côtés sont là, et le cahier d'entrevues fonctionne sans lui.
    "auto_install": True,
    "description": """
Recrutement : dépenses et coût par embauche
===========================================

Ce que ça ajoute
----------------

* **Une clé analytique sur le poste** (`hr.job` reçoit `analytic.mixin`). Les
  débours d'un poste à pourvoir cessent d'être une masse indistincte dans les
  notes de frais.
* **Un poste sur la note de frais** (`hr.expense.job_id`), qui reporte la
  distribution analytique du poste quand la dépense n'en a pas encore.
* **Le temps de panel, valorisé sans aucune saisie de plus.** `bf.interview`
  porte déjà `duration` et `interviewer_ids` : leur produit donne les
  heures-personnes. Rien à saisir, rien à recouper avec les feuilles de temps.
* **Le coût par embauche** : débours plus temps, divisé par le nombre
  d'embauches du poste.

Le taux : l'employé d'abord, la société en repli
------------------------------------------------

`hr_hourly_cost` est un module du coeur qui pose `hourly_cost` sur l'employé.
Quand un membre de panel a un dossier d'employé dans la société du poste et un
taux non nul, c'est ce taux qui vaut. Sinon, le module retombe sur le taux de
repli de la société, à régler dans Recrutement > Configuration.

⚠️ **Un membre de panel n'est pas forcément un employé.** Le panel est fait de
`res.users` : un administrateur, un consultant externe, un membre du conseil
peuvent siéger sans dossier d'employé. Sans taux de repli, leurs heures ne se
valorisent pas.

Le chiffre dit ce qui lui manque
--------------------------------

🔴 **C'est la propriété qui fait tout le module.** Un coût par embauche qui
compte zéro pour les heures non valorisées ment vers le bas, et il ment
d'autant plus que le poste a mobilisé de monde. Le module ne fait jamais ça :

* `panel_hours_unpriced` compte les heures-personnes qu'aucun taux ne couvre ;
* `cost_is_partial` passe à vrai dès qu'il en reste une ;
* `cost_warning` écrit la phrase qui dit combien d'heures manquent, et pourquoi.

Un poste sans aucune embauche n'affiche pas un coût par embauche de zéro : il
n'en affiche aucun, et dit que la dépense engagée court toujours.

Ce que le module ne fait pas
----------------------------

* Il ne touche pas aux feuilles de temps. Le sondage voulait que le temps des
  gens compte ; la mesure existait déjà dans le cahier d'entrevues.
* Il ne compte que les séances **tenues**. Une séance planifiée n'a rien coûté,
  une séance annulée non plus.
* Il écarte les dépenses **refusées**. Une dépense refusée n'est pas un débours.
* Il ne stocke aucun de ses calculs. Ils se lisent à l'affichage, sur des
  données que d'autres modules écrivent. Un champ stocké aurait à se
  recalculer à chaque notation déposée et à chaque note de frais approuvée, et
  Ce module a déjà payé une fois le prix d'un champ stocké qui mentait.

Ce qui reste hors du chiffre
----------------------------

⚠️ Le temps du recruteur hors séance (le tri des CV, les appels, la rédaction)
n'est mesuré nulle part et n'entre donc pas. Le module compte ce qu'il sait
compter et ne prétend pas au reste.

Vie privée
----------

🔴 `hourly_cost` est réservé aux gestionnaires RH par le coeur
(`groups="hr.group_hr_user"`). Les champs de ce module qui en dérivent,
le coût du panel, le coût total et le coût par embauche, portent la **même**
restriction. Sinon le module rendrait, par arithmétique, une donnée salariale
que le coeur protège : deux personnes au panel, une durée connue, et le taux se
déduit. Les heures, elles, restent visibles au recrutement.
""",
    "depends": [
        "bf_recruitment",
        "hr_expense",
        "hr_hourly_cost",
    ],
    "data": [
        "views/res_config_settings_views.xml",
        "views/hr_expense_views.xml",
        "views/hr_job_views.xml",
    ],
}
