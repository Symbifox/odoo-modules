{
    "name": "BF Recherche universelle",
    # 18.0.2.4.0: les libellés sont écrits en anglais dans la source, et
    #   fr_CA.po porte le français. Odoo ne traduit jamais vers en_US,
    #   la langue source : un usager réglé en anglais lisait le module
    #   en français.
    "version": "18.0.2.4.0",
    "category": "Productivity",
    "summary": "Recherche transversale dans tous les modules via la palette de commandes",
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'LGPL-3',
    "depends": ["web", "base", "base_setup", "bf_onboarding_base"],
    "data": [
        "security/ir.model.access.csv",
        "data/bf_onboarding.xml",
        "views/res_users_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_universal_search/static/src/scss/universal_search.scss",
            "bf_universal_search/static/src/js/universal_search_provider.js",
            "bf_universal_search/static/src/js/command_palette_patch.js",
            "bf_universal_search/static/src/js/universal_search_systray.js",
            "bf_universal_search/static/src/xml/universal_search.xml",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
