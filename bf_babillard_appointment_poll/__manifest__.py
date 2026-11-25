# -*- coding: utf-8 -*-
{
    "name": "Noticeboard: availability poll",
    "summary": "Announce in the feed that an availability poll is looking for answers",
    # 18.0.1.0.1: l'état du sondage cité dans le refus de publier se lit dans
    #   la langue de qui publie ; bf_appointment_poll 18.0.1.17.0 a passé sa
    #   source en anglais, et le libellé brut de la sélection était anglais.
    # 18.0.1.0.2: labels written in English in the source, French in fr_CA.po:
    #   Odoo never translates into en_US, the source language.
    "version": "18.0.1.0.2",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_babillard", "bf_appointment_poll"],
    "data": ["views/appointment_poll_views.xml"],
    "auto_install": True,
    "installable": True,
    "description": """
Noticeboard ↔ Availability poll bridge
======================================

Installs itself when both `bf_babillard` and the availability poll are present.

An availability poll is addressed to people who have no account: each of them
answers through a link of their own. The noticeboard is addressed to the
company. This bridge does the one thing that honestly crosses that border: it
announces in the feed that a poll is looking for answers.

🔴 The card NEVER carries a participant's voting link: that link is personal,
and posting it on a card the whole company reads would lend one person's voice
to everybody. When the poll offers open sign-up, that link, public by
construction, is the one the card carries. Otherwise the card announces without
a link, and the organiser sends the invitations.
""",
}
