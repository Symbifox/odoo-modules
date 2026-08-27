{
    "name": "Expérience client - tuile tableau de bord",
    "summary": "Tuile NPS et détracteurs à traiter sur le tableau de bord Symbifox",
    "version": "18.0.1.1.1",
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

S'auto-installe quand bf_cx et bf_dashboard sont tous deux installés.
Ajoute une tuile « NPS 30 jours » (score, détracteurs à traiter, plaintes
ouvertes) dans la rangée « Actions requises » du tableau de bord.
""",
    "depends": [
        "bf_cx",
        "bf_dashboard",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_cx_dashboard/static/src/xml/bf_cx_dashboard.xml",
        ],
    },
}
