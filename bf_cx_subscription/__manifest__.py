{
    "name": "Expérience client : revenu récurrent à risque",
    "summary": "Revenu récurrent des clients à risque sur la tuile Expérience client",
    "version": "18.0.1.0.1",
    "category": "Marketing/Customer Experience",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Pont Expérience client ↔ Abonnements
====================================

S'auto-installe quand bf_cx_dashboard et bf_subscription sont tous deux
installés. Ajoute sur la tuile Expérience client du tableau de bord un
indicateur « revenu récurrent à risque » : la somme des coûts mensualisés
des abonnements actifs gérés pour des clients qui ont un feedback à
rappeler ou une plainte ouverte. Lecture seule, aucun envoi au client.
""",
    "depends": [
        "bf_cx_dashboard",
        "bf_subscription",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_cx_subscription/static/src/xml/bf_cx_subscription.xml",
        ],
    },
}
