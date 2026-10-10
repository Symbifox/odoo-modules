{
    "name": "Expérience client : feedback post-rendez-vous",
    "summary": "Demande de feedback (3 émojis) quand un rendez-vous est terminé",
    # 18.0.1.2.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    "version": "18.0.1.2.0",
    "post_init_hook": "post_init_hook",
    "category": "Marketing/Customer Experience",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Pont Expérience client / Rendez-vous
====================================

S'auto-installe quand bf_cx et bf_appointment sont tous deux installés.
Quand un rendez-vous est terminé, une demande de feedback à 3 émojis
(module rating) part au contact du rendez-vous, si l'option est activée
dans les paramètres (« Feedback après rendez-vous », désactivée par
défaut) ET que le contact n'a pas été sollicité récemment (garde-fou
anti-sursollicitation de bf_cx). Une seule demande par rendez-vous, et
seulement pour les rendez-vous terminés récemment : activer l'option ne
déclenche aucun envoi sur l'historique. La note reçue est ingérée
automatiquement dans le registre des feedbacks.
""",
    "depends": [
        "bf_cx",
        "bf_appointment",
    ],
    "data": [
        "data/mail_template_data.xml",
        "views/res_config_settings_views.xml",
    ],
}
