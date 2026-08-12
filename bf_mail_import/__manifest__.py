{
    "name": "BF Import courriel (.eml)",
    "version": "18.0.1.3.1",
    "category": "Productivity/Email",
    "summary": "Importer des fichiers .eml dans le chatter Odoo",
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://bluefoxconsultant.com',
    'license': 'LGPL-3',
    "depends": ["mail", "bf_onboarding_base"],
    "data": [
        "security/ir.model.access.csv",
        "wizard/mail_import_wizard_views.xml",
        "data/bf_onboarding.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_mail_import/static/src/js/chatter_import_patch.js",
            "bf_mail_import/static/src/xml/chatter_import_patch.xml",
        ],
    },
    "application": False,
    "installable": True,
}
