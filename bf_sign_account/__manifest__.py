{
    'name': "Symbifox — Signature pour la comptabilité",
    'version': '18.0.1.0.1',
    'category': 'Accounting/Accounting',
    'summary': "Envoyer une facture client / facture fournisseur pour signature électronique (bf_sign).",
    'description': """
Ajoute « Envoyer pour signature » sur les pièces comptables (factures clients,
factures fournisseurs) : la facture est rendue en PDF, une demande de signature
bf_sign liée est créée, et le document signé est reversé dans le fil de la pièce
une fois signé par tous.
""",
    'author': "Les services de consultation Blue Fox, Inc.",
    'website': "https://symbifox.com",
    'license': 'Other proprietary',
    'depends': ['bf_sign', 'account'],
    'data': [
        'views/account_move_views.xml',
    ],
    'installable': True,
    'application': False,
}
