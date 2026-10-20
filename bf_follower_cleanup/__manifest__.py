{
    "name": "BF — Hygiène des notifications (abonnés et activités internes)",
    "summary": "Crons qui retirent les abonnés non-employés et les abonnements "
               "aux pistes qu'on ne vend pas, et garde-fou qui n'envoie jamais "
               "une notification d'activité à un compte portail",
    # 18.0.2.3.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    "version": "18.0.2.3.0",
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
