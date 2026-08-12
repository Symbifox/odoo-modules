{
    'name': "Blue Fox — Signature pour les ventes",
    'version': '18.0.1.0.1',
    'category': 'Sales/Sales',
    'summary': "Envoyer un devis / bon de commande pour signature électronique (bf_sign).",
    'description': """
Ajoute « Envoyer pour signature » sur les commandes de vente : le devis est
rendu en PDF, une demande de signature bf_sign liée est créée, et le document
signé est reversé dans le fil de la commande une fois signé par tous.
""",
    'author': "Les services de consultation Blue Fox, Inc.",
    'website': "https://bluefoxconsultant.com",
    'license': 'Other proprietary',
    'depends': ['bf_sign', 'sale'],
    'data': [
        'views/sale_order_views.xml',
    ],
    'installable': True,
    'application': False,
}
