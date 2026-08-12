{
    "name": "Expérience client - feedback post-rencontre",
    "summary": "Demande de feedback (3 émojis) après l'envoi d'un compte rendu",
    "version": "18.0.1.2.0",
    "post_init_hook": "post_init_hook",
    "category": "Marketing/Customer Experience",
    "author": "Les services de consultation Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Pont Expérience client ↔ Rencontres
===================================

S'auto-installe quand bf_cx et bf_meeting sont tous deux installés.
Quand un compte rendu est envoyé au client, une demande de feedback à
3 émojis (module rating) part au partenaire du projet - si l'option est
activée dans les paramètres ET que le contact n'a pas été sollicité
récemment (garde-fou anti-sursollicitation de bf_cx). La note reçue est
ingérée automatiquement dans le registre des feedbacks avec le canal
« Rencontre ».
""",
    "depends": [
        "bf_cx",
        "bf_meeting",
    ],
    "data": [
        "data/mail_template_data.xml",
        "views/res_config_settings_views.xml",
    ],
}
