{
    "name": "Expérience client - digest quotidien",
    "summary": "Section Expérience client (détracteurs, plaintes, NPS) dans le digest quotidien",
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
Pont Expérience client ↔ Digest quotidien
=========================================

S'auto-installe quand bf_cx et daily_todo_digest sont tous deux installés.
Injecte une section « Expérience client » dans le digest quotidien :
détracteurs à traiter, plaintes ouvertes et NPS 30 jours. La section
n'apparaît que s'il y a quelque chose d'actionnable.
""",
    "depends": [
        "bf_cx",
        "daily_todo_digest",
    ],
}
