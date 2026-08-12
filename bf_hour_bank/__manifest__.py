{
    'name': 'Banque d\'heures',
    'version': '18.0.1.15.0',
    'category': 'Services/Project',
    'summary': 'Suivi automatisé des banques d\'heures client',
    'description': """
Banque d'heures
===============

Suivi automatisé des banques d'heures pour les clients en mode forfaitaire.

Fonctionnalités:
-----------------
* Configuration par client (projets, produits de facturation)
* Calcul automatique du solde (débits feuilles de temps, crédits factures)
* Génération de rapports PDF et Excel
* Envoi par courriel avec pièces jointes
* Sommaire par projet et synthèse mensuelle
    """,
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'Other proprietary',
    'depends': ['project', 'account', 'hr_timesheet', 'mail', 'portal', 'bf_onboarding_base'],
    'external_dependencies': {
        'python': ['openpyxl'],
    },
    'data': [
        # Security first
        'security/hour_bank_security.xml',
        'security/ir.model.access.csv',
        # Reports
        'report/hour_bank_paperformat.xml',
        'report/hour_bank_report_templates.xml',
        # Data
        'data/hour_bank_mail_template.xml',
        'data/hour_bank_cron.xml',
        'data/bf_onboarding.xml',
        # Wizards
        'wizard/hour_bank_send_wizard_views.xml',
        # Views
        'views/hour_bank_client_views.xml',
        'views/hour_bank_threshold_views.xml',
        'views/hour_bank_portal_templates.xml',
        'views/menu_views.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
