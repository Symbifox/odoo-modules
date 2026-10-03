{
    "name": "SMS & Calls",
    "summary": "Messagerie SMS/MMS live via VOIP.ms + archivage de SMS et journaux d'appels Android",
    # 18.0.5.26.0 : le contenu d'un SMS archivé
    #   ne se réécrit plus hors superutilisateur (un co-usager de la ligne
    #   partagée réécrivait le corps) ; une pièce MMS ne s'ajoute qu'à ses
    #   propres messages et ne se modifie plus ; l'endpoint de poussée ne se
    #   reprend au nom d'un autre usager qu'avec les clés du même abonnement, et
    #   un abonnement désactivé se réactive ; l'empreinte de dédoublonnage vaut
    #   par fil (UNIQUE(thread_id, message_hash)) : le même SMS reçu par deux
    #   personnes n'est plus avalé chez la deuxième. Hors
    #   superutilisateur, un SMS ne se crée que dans ses propres fils (un
    #   co-usager forgeait un SMS « reçu » dans le fil de la propriétaire), et
    #   `push_subscribe` dit au client qu'il a été refusé. 18.0.5.25.0 n'a
    #   jamais été publiée.
    "version": "18.0.5.26.0",
    "category": "Tools",
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'Other proprietary',
    "application": True,
    "installable": True,
    "depends": [
        "base",
        "mail",
        "project",
        "bf_onboarding_base",
        "bf_chatter_target",
    ],
    "external_dependencies": {
        "python": ["defusedxml", "requests", "pywebpush"],
    },
    "post_init_hook": "post_init_hook",
    "data": [
        "security/sms_security.xml",
        "security/ir.model.access.csv",
        "report/sms_paperformat.xml",
        "report/sms_report_templates.xml",
        "wizard/import_wizard_views.xml",
        "wizard/post_to_task_wizard_views.xml",
        "views/sms_thread_views.xml",
        "views/sms_link_views.xml",
        "views/sms_message_views.xml",
        "views/call_views.xml",
        "views/sms_dashboard_views.xml",
        "views/menu_views.xml",
        "views/sms_device_views.xml",
        "views/sms_line_views.xml",
        "views/res_config_settings_views.xml",
        "views/sms_messenger_views.xml",
        "views/project_task_views.xml",
        # Data
        "data/sms_nc_watch_cron.xml",
        "data/voipms_poll_cron.xml",
        "data/bf_onboarding.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_sms_archive/static/src/js/sms_dashboard.js",
            "bf_sms_archive/static/src/xml/sms_dashboard.xml",
            "bf_sms_archive/static/src/messenger/messenger.js",
            "bf_sms_archive/static/src/messenger/messenger.xml",
            "bf_sms_archive/static/src/messenger/messenger.scss",
            "bf_sms_archive/static/src/systray/sms_systray.js",
            "bf_sms_archive/static/src/systray/sms_systray.xml",
            "bf_sms_archive/static/src/systray/sms_systray.scss",
        ],
    },
}
