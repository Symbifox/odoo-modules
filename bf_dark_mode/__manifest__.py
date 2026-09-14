# -*- coding: utf-8 -*-
{
    'name': 'Symbifox Dark Mode',
    # 18.0.1.5.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    'version': '18.0.1.5.0',
    'summary': 'Dark mode for the Symbifox Odoo backend, using the BF brand gray palette.',
    'category': 'Tools',
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'LGPL-3',
    'depends': ['web', 'mail'],
    'data': [
        'views/res_users_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'bf_dark_mode/static/src/js/dark_mode_state.js',
            'bf_dark_mode/static/src/js/dark_mode_button.js',
            'bf_dark_mode/static/src/js/mail_dark_message.js',
            'bf_dark_mode/static/src/js/mail_inline_patch.js',
            'bf_dark_mode/static/src/scss/dark_mode.scss',
            'bf_dark_mode/static/src/xml/dark_mode_button.xml',
        ],
    },
    'installable': True,
}
