{
    "name": "BF Color",
    "version": "18.0.1.1.1",
    "category": "Hidden/Tools",
    "summary": "Free colors resolved per user, per company and by automatic rules, with saved swatches",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["base", "web"],
    "data": [
        "security/bf_color_security.xml",
        "security/ir.model.access.csv",
        "views/bf_color_views.xml",
        "views/res_partner_category_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_color/static/src/**/*.js",
            "bf_color/static/src/**/*.xml",
            "bf_color/static/src/scss/bf_color.scss",
        ],
    },
    "installable": True,
    "application": False,
}
