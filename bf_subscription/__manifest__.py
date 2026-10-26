{
    'name': "Abonnements",
    # 18.0.1.7.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    # 18.0.1.8.0: le récapitulatif part sous la mise en page commune
    #   (bf_onboarding_base.bf_mail_layout) au lieu de sa propre coquille, la
    #   date de renouvellement stockée est recalculée chaque jour (elle restait
    #   au passé), et la marge par client ne compte que le refacturé.
    # 18.0.1.8.1: un récapitulatif ne se lit que dans sa société, et seul un
    #   gestionnaire l'envoie à ses destinataires.
    # 18.0.1.8.2: le sommaire donne aussi la dépense de la société seule, sans les
    #   abonnements tenus pour un client, et le sujet porte le jour du corps.
    'version': '18.0.1.8.2',
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
    'license': 'Other proprietary',
    'depends': [
        'bf_color',
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
        'data/subscription_activity_data.xml',
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
