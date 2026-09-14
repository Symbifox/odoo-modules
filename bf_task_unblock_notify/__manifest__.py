{
    "name": "BF Notification de d\u00e9blocage de t\u00e2che",
    "summary": "Notifie les assign\u00e9s quand leur t\u00e2che est d\u00e9bloqu\u00e9e",
    # 18.0.1.8.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    "version": "18.0.1.8.0",
    "category": "Project",
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'LGPL-3',
    "depends": ["project", "bf_onboarding_base"],
    "data": [
        "data/unblock_notify_template.xml",
        "data/bf_onboarding.xml",
    ],
    "installable": True,
    "auto_install": False,
}
