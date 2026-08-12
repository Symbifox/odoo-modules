{
    "name": "Expérience client : feedback post-signature",
    "summary": "Demande de feedback (3 émojis) quand une signature est complétée",
    "version": "18.0.1.1.0",
    "post_init_hook": "post_init_hook",
    "category": "Marketing/Customer Experience",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://bluefoxconsultant.com",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Pont Expérience client ↔ Signature électronique
===============================================

S'auto-installe quand bf_cx et bf_sign sont tous deux installés.
Quand une demande de signature est complétée (document scellé par le
dernier signataire), une demande de feedback à 3 émojis (module rating)
part au signataire principal, si l'option est activée dans les
paramètres ET que le contact n'a pas été sollicité récemment (garde-fou
anti-sursollicitation de bf_cx). Une seule demande par dossier de
signature. La note reçue est ingérée automatiquement dans le registre
des feedbacks.
""",
    "depends": [
        "bf_cx",
        "bf_sign",
    ],
    "data": [
        "data/mail_template_data.xml",
        "views/res_config_settings_views.xml",
    ],
}
