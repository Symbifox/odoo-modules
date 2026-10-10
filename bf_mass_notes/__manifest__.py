{
    "name": "Symbifox — Notes en lot",
    "summary": "Ajouter une note (ou un message) à plusieurs fils de discussion en lot "
               "depuis le menu Action des vues liste",
    # 18.0.1.1.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    "version": "18.0.1.1.0",
    "category": "Productivity",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["mail"],
    "data": [
        "security/ir.model.access.csv",
        "wizard/mass_note_wizard_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "application": False,
    "auto_install": False,
}
