{
    "name": "Expérience client - sondage post-perte CRM",
    "summary": "Sondage « pourquoi » automatique quand une opportunité est perdue",
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
Pont Expérience client ↔ CRM
============================

S'auto-installe quand bf_cx et crm sont tous deux installés.
Quand une opportunité est marquée perdue, le sondage du programme désigné
dans les paramètres (« Programme post-perte ») est envoyé au contact -
une seule fois par opportunité, et seulement si le contact n'a pas été
sollicité récemment (garde-fou anti-sursollicitation de bf_cx). Le motif
de perte est consigné au chatter de l'opportunité avec l'envoi.
""",
    "depends": [
        "bf_cx",
        "crm",
    ],
    "data": [
        "views/res_config_settings_views.xml",
    ],
}
