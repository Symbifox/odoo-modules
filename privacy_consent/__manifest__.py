{
    "name": "Suivi des consentements (Loi 25)",
    "version": '18.0.4.3.1',
    "category": "Privacy/Compliance",
    "summary": "Vie privée, consentements et destruction documentaire (Loi 25)",
    "description": """
Vie privée — Loi 25 du Québec
==============================

Module complet de gestion de la vie privée pour la conformité à la Loi 25.

Consentements :
---------------
* Tableau de bord avec KPIs
* Modèles de consentement versionnés avec hash d'intégrité
* Suivi par contact et projet, préférences DNC
* Portail client avec renouvellement
* Intégration DocuSeal et LibreSign (signatures électroniques)
* Séquences de courriels automatisées
* Gestion du consentement des mineurs (<14 ans)
* Piste de vérification forensique avec preuves

Destruction et anonymisation documentaire :
--------------------------------------------
* Calendrier de conservation par type de document (Art. 3.2 LPRPSP)
* Classification documentaire (catégories de RP, sensibilité)
* Registre de destruction immuable avec hash SHA-256
* Campagnes de destruction en lot
* Évaluations d'anonymisation — 3 critères (Règl. A-2.1, r. 0.1)
* Droit à l'effacement (Art. 28.1 LPRPSP)
* Certificats de destruction PDF bilingues
* Effacement sécurisé des identifiants (credentials)
    """,
    'author': 'Les services de consultation Les services de consultation Blue Fox, Inc.',
    "website": "https://symbifox.com",
    'license': 'Other proprietary',
    "depends": ["base", "mail", "project", "portal", "bluefox_branding"],
    "external_dependencies": {
        "python": ["cryptography", "dateutil"],
    },
    "data": [
        # Sécurité en premier
        "security/privacy_security.xml",
        "security/ir.model.access.csv",
        # Données
        "data/privacy_framework_loi25_data.xml",
        "data/privacy_retention_calendar_data.xml",
        "data/privacy_destruction_register_cron.xml",
        "data/privacy_purpose_data.xml",
        "data/privacy_notice_data.xml",
        "data/privacy_cron.xml",
        "data/privacy_retention_cron.xml",
        "data/mail_template.xml",
        "data/mail_template_sequence.xml",
        "data/mail_activity_type.xml",
        # Vues
        "views/privacy_purpose_views.xml",
        "views/privacy_notice_views.xml",
        "views/privacy_consent_views.xml",
        "views/privacy_consent_group_views.xml",
        "views/privacy_evidence_views.xml",
        "views/privacy_preference_views.xml",
        "views/privacy_dashboard_views.xml",
        "views/privacy_retention_views.xml",
        "views/privacy_destruction_views.xml",
        "views/privacy_retention_calendar_views.xml",
        "views/privacy_document_classification_views.xml",
        "views/privacy_destruction_register_views.xml",
        "views/privacy_anonymization_assessment_views.xml",
        "views/privacy_destruction_campaign_views.xml",
        "views/privacy_docuseal_views.xml",
        "views/privacy_libresign_views.xml",
        "views/privacy_email_sequence_views.xml",
        "views/res_partner_views.xml",
        "views/project_views.xml",
        "views/menu_views.xml",
        "views/privacy_framework_views.xml",
        "views/portal_templates.xml",
        # Rapports
        "report/privacy_destruction_certificate.xml",
        "report/privacy_consent_certificate.xml",
        # Assistants
        "wizards/privacy_consent_request_wizard_views.xml",
        "wizards/privacy_consent_withdraw_wizard_views.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
    "post_init_hook": "post_init_hook",
}
