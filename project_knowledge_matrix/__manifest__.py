{
    'name': 'Project Knowledge Matrix',
    'version': '18.0.10.3.0',
    'category': 'Services/Project',
    'summary': 'Base de connaissances projets, politiques et documentation',
    'description': """
Project Knowledge Matrix / Base de connaissances
================================================

Gérez les matrices de connaissances et la documentation d'entreprise.

Fonctionnalités:
----------------
* Tableau de bord KPI avec indicateurs clés cliquables
* Matrices de suivi des décisions de projet
* Import depuis fichiers CSV
* Indicateurs visuels de progression
* Collaboration complète (chatter, activités)
* Système de modèles réutilisables
* Actions groupées pour gestion efficace
* Stockage sécurisé des identifiants avec chiffrement
* Rotation des mots de passe avec audit
* Suivi des versions de documents clients et internes
* Distribution et accusé de réception des documents
* Gestion des politiques et procédures internes
* Suivi de conformité pour les employés
* Catalogage des logiciels documentés (Nextcloud, Odoo, etc.)
* Suivi des versions de logiciels et leur statut de support
* Dates d'expiration et de révision avec rappels automatiques
* Alertes pour documentation obsolète chez les clients
* Courriels de notification stylisés Symbifox
* Rapport bimensuel du tableau de bord par courriel
* Déclenchement manuel des rapports depuis les paramètres
* Traductions complètes en français canadien (fr_CA)
* Suivi formel des décisions avec métadonnées (ADR pattern)
* Gouvernance corporative: résolutions, administrateurs, dirigeants
* Calendrier de conformité corporative avec rappels automatiques
* Livre des minutes intégré à la gestion documentaire
* Rapport PDF brandé Symbifox pour les matrices de connaissances
* Envoi de rapports par courriel (manuel et automatisé)
* Planification flexible : hebdomadaire, bimensuel, mensuel ou intervalle personnalisé
* Activités de suivi automatiques pour les échéances et éléments en retard
    """,
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'LGPL-3',
    # bf_onboarding_base porte les champs de marque (report_brand_*) sur
    # res.company. bluefox_branding n'est PAS requis: c'est le panneau de
    # marque blanche, optionnel. Sans lui, les documents sortent aux couleurs
    # par défaut de l'instance.
    'depends': ['project', 'mail', 'hr', 'bf_onboarding_base'],
    'external_dependencies': {
        'python': ['cryptography'],
    },
    'data': [
        # Security first
        'security/knowledge_security.xml',
        'security/ir.model.access.csv',
        'security/document_body_security.xml',
        # Reference data
        'data/knowledge_section_data.xml',
        'data/credential_type_data.xml',
        'data/credential_cron.xml',
        'data/document_type_data.xml',
        'data/document_section_type_data.xml',
        'data/document_type_section_data.xml',
        'data/document_cron.xml',
        'data/document_mail_templates.xml',
        'data/document_report_mail_template.xml',
        'data/corporate_compliance_data.xml',
        'data/corporate_cron.xml',
        'data/knowledge_activity_type.xml',
        'data/knowledge_activity_cron.xml',
        'data/matrix_report_cron.xml',
        # Reports
        'report/knowledge_matrix_paperformat.xml',
        'report/knowledge_matrix_report_templates.xml',
        'report/corporate_resolution_templates.xml',
        'report/document_body_templates.xml',
        # Wizards
        'wizard/import_csv_wizard_views.xml',
        'wizard/matrix_send_wizard_views.xml',
        # Views
        'views/knowledge_section_views.xml',
        'views/knowledge_item_views.xml',
        'views/knowledge_matrix_views.xml',
        'views/credential_type_views.xml',
        'views/credential_views.xml',
        'views/document_type_views.xml',
        'views/document_software_views.xml',
        'views/document_views.xml',
        'views/raci_stakeholder_views.xml',
        'views/project_views.xml',
        'views/corporate_resolution_views.xml',
        'views/corporate_director_views.xml',
        'views/corporate_officer_views.xml',
        'views/corporate_compliance_views.xml',
        # Actions de forage du rapport courriel: charge APRÈS toutes les vues
        # qu'elle référence en search_view_id (document, distribution,
        # identifiant, élément de connaissance, et les quatre corporatives).
        'views/report_drilldown_actions.xml',
        'views/dashboard_views.xml',
        'views/res_config_settings_views.xml',
        'views/menu_views.xml',
        # Corps documentaire: charge APRÈS menu_views.xml, le menu de
        # configuration des sections s'accroche à menu_knowledge_config.
        'views/document_body_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'project_knowledge_matrix/static/src/js/credential_copy.js',
            'project_knowledge_matrix/static/src/xml/credential_copy.xml',
            'project_knowledge_matrix/static/src/js/knowledge_dashboard.js',
            'project_knowledge_matrix/static/src/xml/knowledge_dashboard.xml',
            'project_knowledge_matrix/static/src/js/chatter_knowledge_action.js',
            'project_knowledge_matrix/static/src/js/attachment_preview.js',
            'project_knowledge_matrix/static/src/xml/attachment_preview.xml',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
}
