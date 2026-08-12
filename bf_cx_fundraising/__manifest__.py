{
    "name": "Expérience client : sondage donateur",
    "summary": "Sondage d'expérience donateur après la confirmation d'un don",
    "version": "18.0.1.0.1",
    "category": "Marketing/Customer Experience",
    "author": "Les services de consultation Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "AGPL-3",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Pont Expérience client / Collecte de fonds
==========================================

S'auto-installe quand bf_cx et bf_fundraising_core sont tous deux
installés. Fonctionnalité produit destinée aux OBNL qui utilisent la
suite de collecte de fonds : quand un don est validé, le sondage du
programme désigné dans les paramètres (« Programme expérience
donateur ») est envoyé au donateur. Une seule fois par don, et
seulement si le donateur n'a pas été sollicité récemment (garde-fou
anti-sursollicitation de bf_cx). Un donateur fidèle donne souvent : la
cadence minimale du programme est la protection principale, 90 jours
sont recommandés. Vide = aucun envoi (défaut).
""",
    "depends": [
        "bf_cx",
        "bf_fundraising_core",
    ],
    "data": [
        "views/res_config_settings_views.xml",
    ],
}
