# -*- coding: utf-8 -*-
{
    'name': 'Symbifox Dark Mode',
    'version': '18.0.1.1.0',
    'summary': 'Dark mode for the Symbifox Odoo backend, using the BF brand gray palette.',
    'category': 'Tools',
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'LGPL-3',
    'depends': ['web'],
    'data': [
        'views/res_users_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'bf_dark_mode/static/src/js/dark_mode_button.js',
            'bf_dark_mode/static/src/scss/dark_mode.scss',
            'bf_dark_mode/static/src/xml/dark_mode_button.xml',
        ],
    },
    'installable': True,
}
