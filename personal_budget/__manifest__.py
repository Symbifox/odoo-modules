{
    'name': 'Personal Budget',
    'version': '18.0.2.1.2',
    'category': 'Productivity',
    'summary': "Household budget: personal or shared budgets, subscriptions and dashboard",
    'description': """
Personal budget for a household instance
========================================

Budget module independent from Odoo accounting (it only depends on ``base``
and ``web``): transactions, budget plans, recurring expenses and
subscriptions, loans, cheques, freelance invoices, sharing ledger and a
dashboard.

**Several people in one database.** Every record belongs to a *budget*. A
budget is visible to its owner and to the people the owner explicitly shared
it with, and to nobody else (administrators included; a system administrator
can still lift the rules themselves). Each person gets a
personal budget on first use; a couple budget can be created next to it
and shared explicitly.

**Household subscriptions.** Streaming, insurance, mobile phone: a recurring
expense (amount, frequency, first payment date, category) feeds the dashboard
forecast, shows what is left to pay this year and turns into a transaction
when due, by hand or automatically.

No seed data: each person creates their own categories.

Labels are written in English; French (Canada) comes from ``i18n/fr_CA.po``.
See README.md for the version history and the migration from 18.0.1.x.
    """,
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    # `web` : les actifs du tableau de bord (OWL) doivent passer APRÈS le chargeur
    # de modules JS d'Odoo. Avec `base` seul, le paquet les plaçait en tête et
    # tout le client web tombait (« odoo.define is not a function »).
    'depends': ['base', 'web'],
    'data': [
        # Security
        'security/budget_groups.xml',
        'security/ir.model.access.csv',
        'security/budget_record_rules.xml',
        # Data
        'data/budget_cron.xml',
        # Views
        'views/book_views.xml',
        'views/category_views.xml',
        'views/contributor_views.xml',
        'views/transaction_views.xml',
        'views/plan_views.xml',
        'views/recurring_views.xml',
        'views/loan_views.xml',
        'views/cheque_views.xml',
        'views/invoice_views.xml',
        'views/share_line_views.xml',
        'views/dashboard_views.xml',
        'views/import_wizard_views.xml',
        # Menu (last)
        'views/menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'personal_budget/static/src/js/budget_dashboard.js',
            'personal_budget/static/src/xml/budget_dashboard.xml',
        ],
    },
    'icon': '/personal_budget/static/description/icon.png',
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
