# -*- coding: utf-8 -*-
{
    "name": "Symbifox — Téléphone SIP",
    "summary": "Softphone WebRTC (JsSIP) dans le client web, adossé à un PBX Asterisk",
    "version": "18.0.2.10.0",
    "category": "Tools",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "application": True,
    "installable": True,
    # bf_sms_archive fournit call.archive.call (_ingest_one), sms.archive.thread
    # (normalize_phone / appariement partenaire) et get_lines — tout réutilisé ici.
    "depends": ["base", "web", "mail", "bf_sms_archive"],
    "data": [
        "security/softphone_security.xml",
        "security/ir.model.access.csv",
        "views/res_users_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            # ⚠️ JsSIP N'est PAS listé ici : ~240 Ko de tiers ne doivent pas alourdir le
            # bundle de tout le backend. Il est chargé en PARESSEUX (loadJS) par le service,
            # et seulement pour un membre du groupe. Voir static/src/js/softphone_service.js.
            "bf_softphone/static/src/scss/softphone.scss",
            "bf_softphone/static/src/js/softphone_service.js",
            "bf_softphone/static/src/js/softphone_launcher.js",
            "bf_softphone/static/src/js/softphone_panel.js",
            "bf_softphone/static/src/js/phone_field_patch.js",
            "bf_softphone/static/src/xml/softphone.xml",
        ],
    },
}
