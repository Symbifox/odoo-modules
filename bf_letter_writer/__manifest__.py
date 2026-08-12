{
    'name': 'Letter Writer',
    'version': '18.0.2.0.0',
    'category': 'Tools',
    'summary': "Rédaction de lettres officielles brandées : en-tête, fusion de champs, modèles et blocs réutilisables",
    'description': """
Letter Writer
=============

Rédiger des lettres officielles avec un en-tête brandé verrouillé.

Fonctionnalités
---------------
* En-tête de lettre piloté par ``res.company`` (logo, couleurs, signataire,
  pied de page) — fonctionne en marque blanche pour toute société.
* Modèles de lettres réutilisables avec champs de fusion ({{ }} et QWeb).
* Fusion en lot : un modèle, plusieurs destinataires.
* Blocs de texte réutilisables (quicktext) insérables dans le corps.
* Génération de PDF brandé et envoi par courriel avec PDF joint.
* Intégration optionnelle : pré-remplissage depuis ``bf_persona`` et bouton
  « Réviser avec Claude » via ``bf_claude_chat`` (détectés à l'exécution).
    """,
    'author': 'Blue Fox Inc.',
    'website': 'https://bluefoxconsultant.com',
    'license': 'LGPL-3',
    'depends': ['base', 'mail', 'bf_lexend', 'bf_onboarding_base'],
    'external_dependencies': {
        # PyPDF2 powers the "PDF overlay" letterhead mode (stamping the body
        # onto an uploaded letterhead PDF). Shipped in the BF Odoo image.
        'python': ['PyPDF2'],
    },
    'data': [
        # Security first
        'security/letter_security.xml',
        'security/ir.model.access.csv',
        # Sequences
        'data/letter_sequence.xml',
        # Reports
        'report/letter_paperformat.xml',
        'report/letter_report_templates.xml',
        # Starter data
        'data/letter_quicktext_data.xml',
        'data/letter_template_data.xml',
        'data/bf_onboarding.xml',
        # Wizards
        'wizard/letter_merge_wizard_views.xml',
        'wizard/letter_send_wizard_views.xml',
        'wizard/letter_quicktext_picker_views.xml',
        # Views
        'views/letter_template_views.xml',
        'views/letter_document_views.xml',
        'views/letter_quicktext_views.xml',
        'views/res_company_views.xml',
        'views/menu_views.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
