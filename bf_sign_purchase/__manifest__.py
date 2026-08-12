{
    'name': "Symbifox — Signature pour les achats",
    'version': '18.0.1.0.1',
    'category': 'Inventory/Purchase',
    'summary': "Envoyer un bon de commande fournisseur pour signature électronique (bf_sign).",
    'description': """
Ajoute « Envoyer pour signature » sur les bons de commande d'achat : le bon est
rendu en PDF, une demande de signature bf_sign liée est créée, et le document
signé est reversé dans le fil du bon de commande une fois signé.
""",
    'author': "Les services de consultation Les services de consultation Blue Fox, Inc.",
    'website': "https://symbifox.com",
    'license': 'Other proprietary',
    'depends': ['bf_sign', 'purchase'],
    'data': [
        'views/purchase_order_views.xml',
    ],
    'installable': True,
    'application': False,
}
