{
    "name": "BF — Nettoyage des abonnés (followers internes uniquement)",
    "summary": "Cron qui retire toute personne non-employée des abonnés des chatters",
    "version": "18.0.1.0.1",
    "category": "Tools",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["mail"],
    "data": [
        "data/ir_config_parameter.xml",
        "data/cron.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
