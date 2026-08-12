{
    'name': "Abonnements",
    'version': '18.0.1.3.1',
    'category': 'Accounting/Accounting',
    'summary': "Gestion des abonnements payants et corrélation avec la facturation fournisseur",
    'description': """
Abonnements
===========

Registre unique des abonnements payants : SaaS, memberships corporatifs,
infrastructure, certificats, noms de domaine, services professionnels récurrents.

Fonctionnalités:
----------------
* Suivi des abonnements en propre OU gérés au nom d'un client
* Corrélation manuelle et automatique avec les factures fournisseur (account.move)
* Refacturation au client en 3 modes (au coût / markup % / montant fixe)
* Relances automatiques avant renouvellement (activité Odoo)
* Vue dashboard : coût mensualisé, prochains renouvellements, MRR consolidé
* Smart buttons sur la fiche partenaire (abonnements gérés / facturés par)
    """,
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'mail',
        'account',
        'analytic',
        'bf_onboarding_base',
    ],
    'data': [
        # Security first
        'security/subscription_security.xml',
        'security/ir.model.access.csv',
        # Reports
        'report/subscription_report_paperformat.xml',
        'report/subscription_report_templates.xml',
        # Data
        'data/subscription_sequence.xml',
        'data/subscription_tag_data.xml',
        'data/subscription_cron.xml',
        'data/subscription_digest_data.xml',
        'data/bf_onboarding.xml',
        # Wizards
        'wizard/subscription_rebill_wizard_views.xml',
        # Views
        'views/subscription_tag_views.xml',
        'views/subscription_subscription_views.xml',
        'views/subscription_digest_views.xml',
        'views/account_move_views.xml',
        'views/res_partner_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
