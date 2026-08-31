# -*- coding: utf-8 -*-
{
    "name": "Budgets opérationnels — campagnes",
    "version": "18.0.1.1.0",
    "category": "Accounting/Accounting",
    "summary": "Rattache une campagne à un compte analytique : elle connaissait"
               " sa recette, elle connaît enfin sa dépense",
    "description": """
Budgets opérationnels — campagnes
=================================

Odoo sait déjà ce qu'une campagne a **rapporté** : `utm.campaign` porte le
montant facturé, le nombre de pistes et le nombre de devis. Aucun de ses
trente-trois champs n'est une dépense.

Demander « les dépenses marketing par campagne » ne demande donc ni de créer la
campagne, qui existe, ni un registre de dépenses, qui ferait doublon avec la
comptabilité. Ça demande de **rattacher la campagne à un compte analytique**,
pour que la dépense entre par le chemin comptable normal et se retrouve en face
du rendement que la campagne calcule déjà.

Ce que le module ajoute
-----------------------
* **Un compte analytique par campagne**, créé d'un bouton, avec une contrainte
  d'unicité : un compte ne sert jamais deux campagnes, sans quoi les deux
  afficheraient la même dépense.
* **La dépense de la campagne**, séparée entre ce qui est passé par la
  comptabilité et ce qui est du temps interne, selon la règle du socle : les
  deux sources sont **disjointes par construction**, donc aucun dollar n'est
  compté deux fois.
* **Les heures non valorisées** signalées, parce qu'un coût interne nul se lit
  comme du travail gratuit alors que c'est un taux horaire qui manque.
* **Les lignes budgétaires** qui nomment ce compte, avec leur prévu cumulé.

Ce que le module ne fait pas
----------------------------
* Il ne tire aucune dépense d'une API publicitaire. Les connecteurs de
  publication parlent aux API de publication ; la dépense demande les API Ads,
  qui sont d'autres produits avec leur propre revue d'accès. La saisie
  comptable reste le chemin, et le repli.
* Il ne borne pas la campagne dans le temps : c'est la ligne budgétaire qui
  porte une période.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": [
        "bf_budget",
        "utm",
    ],
    "data": [
        "views/res_config_settings_views.xml",
        "views/bf_budget_campaign_views.xml",
    ],
    "application": False,
    "installable": True,
    "auto_install": False,
}
