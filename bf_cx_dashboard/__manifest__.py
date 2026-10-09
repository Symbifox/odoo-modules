{
    "name": "Expérience client - tuile tableau de bord",
    "summary": "Tuile NPS et détracteurs à traiter sur le tableau de bord Symbifox",
    # 18.0.1.2.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    "version": "18.0.1.2.0",
    "category": "Marketing/Customer Experience",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Pont Expérience client ↔ Tableau de bord
========================================

S'auto-installe quand bf_cx et bf_home sont tous deux installés.
Ajoute une tuile « NPS 30 jours » (score, détracteurs à traiter, plaintes
ouvertes) dans la rangée « Actions requises » du tableau de bord.
""",
    "depends": [
        "bf_cx",
        "bf_home",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_cx_dashboard/static/src/xml/bf_cx_dashboard.xml",
        ],
    },
}
