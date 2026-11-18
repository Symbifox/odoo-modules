# -*- coding: utf-8 -*-
{
    "name": "Symbifox Avatars at School",
    # 18.0.1.0.0: first public release. A student under 18 is never handed a
    #   beard, a moustache, grey hair or a bald head by the avatar draw, and
    #   cannot pick one in the builder either.
    "version": "18.0.1.0.0",
    "category": "Hidden/Tools",
    "summary": "A minor student's avatar, without a beard or a bald head",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "application": False,
    "installable": True,
    "auto_install": True,
    "depends": ["bf_avatar", "bf_school_core"],
    "description": """
Symbifox Avatars at School
==========================

The bridge between the house avatars and the student record.

A generated avatar is drawn from the name, so a school that installs
``bf_avatar`` gives a character to every student without a photo. Nothing
stopped that draw from handing a seven year old a full beard, grey hair or a
bald head.

This module takes those parts out of the draw **and out of the builder** for a
student under 18, and for them alone. Nobody else changes: not the staff, not
the guardians, not a student of age.

* **Open Peeps**: all facial hair, and the ``grayBun``, ``grayMedium``,
  ``grayShort``, ``noHair1`` to ``noHair3`` hairstyles. The ``shaved`` ones
  stay: a buzz cut with a full hairline is an ordinary teenage haircut.
* **Notionists**: all facial hair. The style is line art with no colour
  palette, and none of its 64 hairstyles is bald or receding, so there is
  nothing else to take out.

Age is read from the student's date of birth, with sudo, and never from
``is_minor_child`` of ``privacy_consent``: that field means "under the age of
consent", which is 14 in Québec, and reading it here would leave every 14 to
17 year old with a beard.

The draw stays aligned. A barred slot still spends its roll, and the draw is
still made on everybody's pool: a barred person keeps THEIR avatar without the
barred part, rather than being handed a whole new one.
""",
    "post_init_hook": "post_init_hook",
}
