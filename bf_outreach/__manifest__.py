# -*- coding: utf-8 -*-
{
    "name": "Campagnes de démarchage",
    "version": "18.0.1.2.0",
    "category": "Sales/CRM",
    "summary": "Suivi des campagnes de démarchage par appels et courriels, avec cadence de relance",
    "description": """
Campagnes de démarchage
=======================

Piloter une campagne de sollicitation (appels + courriels) sur une liste de
cibles, et savoir en tout temps qui a été contacté, qui est en retard et où
en est chaque dossier.

Fonctionnalités
---------------
* Campagne : responsable, équipe, période, objectifs et cadence de relance
  (nombre d'appels et de courriels visés par cible, intervalle entre deux
  contacts, jours ouvrables seulement, arrêt à la première réponse).
* Cibles : une fiche par dossier, avec étapes configurables (kanban),
  responsable, étiquettes, historique complet et discussion.
* Interactions : chaque appel, courriel, texto ou rencontre est journalisé
  avec son résultat ; les compteurs et les dates de prochaine relance se
  recalculent seuls.
* Vue d'ensemble : compteurs par campagne (couverture, taux de réponse,
  conversion, retards), filtres « à faire aujourd'hui » / « en retard » /
  « jamais contacté », vues calendrier, pivot et graphique.
* Relances : activité quotidienne (par campagne ou par cible) pour les
  contacts dus, afin que personne ne tombe entre deux chaises.
* Passerelle CRM : création d'une opportunité à partir d'une cible qualifiée.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://bluefoxconsultant.com",
    "license": "LGPL-3",
    "depends": ["mail", "contacts", "crm", "phone_validation", "bf_onboarding_base"],
    "data": [
        # Sécurité d'abord
        "security/outreach_security.xml",
        "security/ir.model.access.csv",
        # Données
        "data/outreach_stage_data.xml",
        "data/outreach_cron.xml",
        # Assistants (définis avant les vues qui les appellent)
        "wizard/outreach_log_wizard_views.xml",
        "wizard/outreach_exclude_wizard_views.xml",
        "wizard/outreach_target_import_wizard_views.xml",
        # Vues
        "views/outreach_campaign_views.xml",
        "views/outreach_target_views.xml",
        "views/outreach_touch_views.xml",
        "views/outreach_stage_views.xml",
        "views/outreach_tag_views.xml",
        "views/res_partner_views.xml",
        "views/crm_lead_views.xml",
        "views/menu_views.xml",
        # Actions contextuelles + panneau d'accueil
        "data/outreach_server_action.xml",
        "data/bf_onboarding.xml",
    ],
    "post_init_hook": "post_init_hook",
    "application": True,
    "installable": True,
    "auto_install": False,
}
