{
    "name": "Expérience employé",
    "summary": "Catalogue des avantages, admissibilité par règle, registre d'usage "
               "et indicateurs de rétention",
    "version": "18.0.1.0.0",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "application": True,
    "installable": True,
    "description": """
Expérience employé
==================

Odoo sait qui travaille ici. Il ne sait pas à quoi cette personne a droit, si
elle s'en sert, ni ce que ça coûte. Ce module ajoute cette moitié-là.

Les deux outils d'Odoo qui serviraient le mieux la rétention sont hors de
portée d'une édition communautaire : `hr_appraisal` et le configurateur de
package salarial sont vendus séparément. Ce module ne les remplace pas. Il
couvre l'avantage social, son admissibilité, son usage et son coût.

Ce qu'il ajoute
---------------

* **Un catalogue d'avantages** (`bf.ex.benefit`), avec fournisseur, période de
  validité et l'un des trois modèles de coût : par personne et par année, à
  l'usage, ou forfaitaire annuel.
* **Des règles d'admissibilité** (`bf.ex.eligibility.rule`) exprimées en
  critères lisibles, jamais en domaine brut : département, ancienneté, type
  d'emploi, horaire, poste, lieu, société, gestionnaire. Une personne qui
  conteste son droit doit pouvoir s'entendre expliquer pourquoi.
* **Un droit résolu** (`bf.ex.entitlement`), calculé par les règles ou accordé
  à la main avec un motif écrit. Un droit perdu se ferme avec une date de fin
  et reste lisible ; il ne disparaît pas.
* **Un registre d'usage** (`bf.ex.usage`), qui répond à « qui s'en sert » et
  porte le coût réel. Une ligne confirmée ne se récrit plus.
* **Des demandes** (`bf.ex.claim`) pour les avantages qui exigent une
  approbation, avec un approbateur configurable par avantage.
* **Six indicateurs** (`bf.ex.indicator`) : taux d'adhésion, coût par avantage,
  coût par personne, ancienneté médiane, taux de roulement, et la liste des
  avantages que personne n'utilise.

La porte d'entrée est le droit
------------------------------

Un avantage se donne parce qu'on y a droit, pas parce qu'on l'a mérité. C'est
ce qui distingue ce module d'un catalogue de récompenses : aucun solde de
points n'ouvre quoi que ce soit ici. Les deux mécaniques peuvent coexister
dans une même entreprise ; elles ne se remplacent pas.

Ce qu'il ne fait pas
--------------------

* Il ne porte aucune règle de conservation. C'est le rôle du pont
  `bf_employee_experience_privacy`. Le registre d'usage dit des choses sur la
  santé d'une personne : il se lit par elle et par qui administre les
  avantages, jamais par toute l'entreprise.
* Il ne va rien chercher dans les notes de frais tout seul. C'est le pont
  `bf_employee_experience_expense`.
* Il ne mesure pas l'expérience vécue. C'est `bf_employee_experience_enps`,
  qui exige une collecte anonyme et un seuil de répondants.
""",
    "depends": [
        "hr",
        "hr_contract",
        "mail",
    ],
    "data": [
        "security/employee_experience_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "data/starter_action.xml",
        "views/benefit_views.xml",
        "views/eligibility_rule_views.xml",
        "views/entitlement_views.xml",
        "views/usage_views.xml",
        "views/claim_views.xml",
        "views/indicator_views.xml",
        "views/hr_employee_views.xml",
        "views/menuitems.xml",
        "report/statement_reports.xml",
        "report/statement_templates.xml",
    ],
    "demo": [
        "demo/benefit_demo.xml",
    ],
}
