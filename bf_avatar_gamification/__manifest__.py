# -*- coding: utf-8 -*-
{
    "name": "Symbifox Avatars pour Fox Quest",
    # 18.0.1.0.1: first public release. Avatar parts are sold for XP in the
    #   Fox Quest reward shop, in packs; what says who you are stays free.
    "version": "18.0.1.0.1",
    "category": "Hidden/Tools",
    "summary": "Unlock avatar parts with the XP earned in Fox Quest",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "depends": ["bf_avatar", "bf_gamification"],
    "description": """
Symbifox Avatars for Fox Quest
==============================

Bridges the avatar builder and the Fox Quest reward shop.

* Decorative parts (hats, sunglasses, expressions, outfits, gestures, badges)
  are sold in packs of 25, 50 or 100 XP, as ordinary rewards.
* What says who a person is stays free: hair textures, head coverings,
  glasses, beards, colours.
* A pack is self-service and comes out of the XP balance, not the XP earned:
  it never costs a level.
""",
    "data": [
        "data/avatar_packs.xml",
        "views/gamification_reward_views.xml",
    ],
}
