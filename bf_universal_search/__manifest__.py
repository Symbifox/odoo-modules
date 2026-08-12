{
    "name": "BF Recherche universelle",
    "version": "18.0.2.0.1",
    "category": "Productivity",
    "summary": "Recherche transversale dans tous les modules via la palette de commandes",
    'author': 'Les services de consultation Blue Fox, Inc.',
    'website': 'https://symbifox.com',
    'license': 'LGPL-3',
    "depends": ["web", "base", "bf_onboarding_base"],
    "data": [
        "security/ir.model.access.csv",
        "data/bf_onboarding.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_universal_search/static/src/scss/universal_search.scss",
            "bf_universal_search/static/src/js/universal_search_provider.js",
            "bf_universal_search/static/src/js/universal_search_systray.js",
            "bf_universal_search/static/src/xml/universal_search.xml",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
