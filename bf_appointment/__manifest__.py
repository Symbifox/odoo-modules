{
    "name": "Blue Fox Appointment",
    # 18.0.2.32.0: availability fix — attended events marked show_as='free' no longer block the slot picker (all-day/working-location sync events could blank a morning)
    # 18.0.2.33.0: localisation FR — (1) picker: jours/mois via babel au lieu de strftime %A/%B (locale C = anglais); (2) tous les libellés backend francisés à la source (le fr.po ne se chargeait jamais — mauvais format de référence), incl. champs de base OCA relabellés via override incrémental; (3) ventilation du « Modifications Deadline » OCA en 2 réglages : modifications_deadline = « Préavis minimum avant réservation » (plancher dispo, inchangé) + nouveau modification_lock_hours = « Délai limite de modification/annulation » (verrou, via _compute_is_overdue).
    "version": "18.0.2.34.0",
    "category": "Appointments",
    "summary": "Public self-service booking pages extending Resource Booking",
    'author': 'Blue Fox Inc.',
    'website': 'https://bluefoxconsultant.com',
    'license': 'LGPL-3',
    "depends": ["resource_booking", "portal", "mail", "project", "privacy_consent", "bf_onboarding_base", "bf_timezone"],
    "data": [
        "security/appointment_security.xml",
        "security/ir.model.access.csv",
        "data/appointment_mail_templates.xml",
        "data/appointment_cron.xml",
        "data/appointment_menu.xml",
        "data/bf_onboarding.xml",
        "templates/appointment_public.xml",
        "templates/appointment_confirmation.xml",
        "views/resource_booking_type_views.xml",
        "views/resource_booking_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "bf_appointment/static/src/js/timezone_detect.js",
            "bf_appointment/static/src/js/processing_buttons.js",
            "bf_appointment/static/src/scss/appointment.scss",
        ],
    },
    "installable": True,
    "application": True,
}
