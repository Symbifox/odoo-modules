{
    "name": "Expérience client : CSAT post-maintenance",
    "summary": "Demande de feedback (3 émojis) après une maintenance planifiée",
    "version": "18.0.1.1.0",
    "post_init_hook": "post_init_hook",
    "category": "Marketing/Customer Experience",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "auto_install": True,
    "description": """
Pont Expérience client ↔ Hébergement
====================================

S'auto-installe quand bf_cx et hosting_management sont tous deux installés.
Quand une maintenance planifiée touchant un service client est marquée
faite, une demande de feedback à 3 émojis (module rating) part au client
du service : si l'option est activée dans les paramètres ET que le contact
n'a pas été sollicité récemment (garde-fou anti-sursollicitation de
bf_cx). Les partenaires internes (la société elle-même) sont exclus. Les
planifications étant récurrentes, chaque cycle de maintenance peut
redemander un feedback : l'indicateur d'envoi est réinitialisé à chaque
nouvelle occurrence et le garde-fou anti-sursollicitation encadre la
fréquence des demandes. Le courriel utilise le gabarit brandé bilingue
(FR/EN via le hook i18n partagé de bf_cx) avec lien de désabonnement. La
note reçue est ingérée automatiquement dans le registre des feedbacks.
""",
    "depends": [
        "bf_cx",
        "hosting_management",
    ],
    "data": [
        "data/mail_template_data.xml",
        "views/res_config_settings_views.xml",
    ],
}
