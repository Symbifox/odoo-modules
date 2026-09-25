# -*- coding: utf-8 -*-
{
    "name": "Symbifox Avatars",
    # 18.0.1.0.1: first public release. Anyone without a picture gets an
    #   avatar drawn in the house style, and a person can compose their own
    #   character. Uploaded pictures are never replaced.
    "version": "18.0.1.0.1",
    "category": "Hidden/Tools",
    "summary": "An avatar in your house style for anyone without a photo, and a builder to make your own",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": False,
    "depends": ["base_setup", "web"],
    "description": """
Symbifox Avatars
================

Hardly anyone uploads a photo, so most screens show Odoo's default avatar: a
white initial on a colour picked at random. This module replaces that default,
and only that default.

* **Initials in the house colours**, in shades that keep the letter readable.
* **A character** in one of two hand-drawn styles (Open Peeps, Notionists),
  derived from the name so the same person always gets the same one.
* The style is a database setting (General Settings, Avatars). Changing it
  redraws every generated avatar; an uploaded picture is never touched.
* Contacts get the same treatment instead of a grey silhouette.
* **My avatar**, in the user menu, to compose your own character.

Open Peeps (Pablo Stanley) and Notionists (Zoish) are CC0 1.0. The part
templates were exported from the DiceBear packages (MIT) by
``data/styles/export.mjs``.
""",
    "data": [
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "bf_avatar/static/src/js/avatar_composer.js",
            "bf_avatar/static/src/js/user_menu.js",
            "bf_avatar/static/src/xml/avatar_composer.xml",
            "bf_avatar/static/src/scss/avatar_composer.scss",
        ],
    },
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
}
