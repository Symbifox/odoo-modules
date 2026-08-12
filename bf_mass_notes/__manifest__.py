{
    "name": "Blue Fox — Notes en lot",
    "summary": "Ajouter une note (ou un message) à plusieurs fils de discussion en lot "
               "depuis le menu Action des vues liste",
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "author": "Blue Fox Inc.",
    "website": "https://bluefoxconsultant.com",
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
