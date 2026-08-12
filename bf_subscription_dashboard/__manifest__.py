{
    'name': "Abonnements — carte du tableau de bord",
    'version': '18.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': "Ajoute une carte Abonnements au tableau de bord Symbifox",
    'description': """
Module-pont : ajoute une carte de synthèse des abonnements (dépense mensualisée,
renouvellements à venir, dormants) au tableau de bord Symbifox (bf_dashboard).

S'installe automatiquement lorsque bf_subscription ET bf_dashboard sont présents.
    """,
    'author': 'Les services de consultation Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'LGPL-3',
    'depends': ['bf_subscription', 'bf_dashboard'],
    'assets': {
        'web.assets_backend': [
            'bf_subscription_dashboard/static/src/js/subscription_card_patch.js',
            'bf_subscription_dashboard/static/src/xml/subscription_card.xml',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': True,
}
