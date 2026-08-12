# -*- coding: utf-8 -*-
{
    'name': 'Symbifox — Préférences de la barre système',
    'version': '18.0.1.0.0',
    'summary': 'Per-user show/hide of systray (notification-tray) icons, via a gear menu',
    'category': 'Tools',
    'author': 'Les services de consultation Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'LGPL-3',
    'depends': ['web'],
    'assets': {
        'web.assets_backend': [
            'bf_systray_prefs/static/src/prefs_service.js',
            'bf_systray_prefs/static/src/navbar_patch.js',
            'bf_systray_prefs/static/src/systray_gear.js',
            'bf_systray_prefs/static/src/systray_gear.xml',
            'bf_systray_prefs/static/src/systray_gear.scss',
        ],
    },
    'installable': True,
    'application': False,
}
