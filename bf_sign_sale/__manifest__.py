{
    'name': "Symbifox — Signature pour les ventes",
    # 18.0.2.2.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    'version': '18.0.2.2.0',
    'category': 'Sales/Sales',
    'summary': "Envoyer un devis / bon de commande pour signature électronique (bf_sign).",
    'description': """
Ajoute « Envoyer pour signature » sur les commandes de vente : le devis est
rendu en PDF, une demande de signature bf_sign liée est créée, et le document
signé est reversé dans le fil de la commande une fois signé par tous.
""",
    'author': "Les services de consultation Blue Fox, Inc.",
    'website': "https://symbifox.com",
    'license': 'Other proprietary',
    'depends': ['bf_sign', 'sale'],
    'data': [
        'views/sale_order_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': True,
}
