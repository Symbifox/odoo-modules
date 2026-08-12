{
    'name': "Abonnements — section du digest quotidien",
    'version': '18.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': "Ajoute une section « Renouvellements à venir » au digest quotidien",
    'description': """
Module-pont : injecte une section des renouvellements d'abonnements à venir
dans le courriel du digest quotidien (daily_todo_digest).

S'installe automatiquement lorsque bf_subscription ET daily_todo_digest sont
tous deux présents.
    """,
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'LGPL-3',
    'depends': ['bf_subscription', 'daily_todo_digest'],
    'data': [
        'views/daily_digest_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': True,
}
