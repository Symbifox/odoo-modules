# -*- coding: utf-8 -*-
{
    "name": "Noticeboard: Odoo surveys",
    "summary": "Announce an Odoo survey in the feed, with its link; and its live session, with its code",
    # 18.0.1.1.1: labels written in English in the source, French in fr_CA.po:
    #   Odoo never translates into en_US, the source language.
    "version": "18.0.1.1.1",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_babillard", "survey"],
    "data": ["views/survey_views.xml"],
    "auto_install": True,
    "installable": True,
    "description": """
Noticeboard ↔ Surveys bridge
============================

Installs itself when both `bf_babillard` and Surveys are present.

The noticeboard carries polls of its own: one question, a few options, one
click. This bridge serves the other need, the questionnaire: an Odoo survey has
pages, sections and scored questions, and it is answered elsewhere. The feed
announces it and gives its link; it does not replay it.

It is a gesture of the editors, not an automatic one: an open questionnaire is
not necessarily company news, and the feed is the one place everybody reads.

⚠️ A survey restricted to invited people is not announced. Its start link
requires a personal invitation: posted on a card the whole company reads, it
leads nobody anywhere.

And the live session, with its code
===================================

A second button, shown only while a session is OPEN: the card then carries the
session code and the short join link. Without it, nothing announces an open
session to the company, and the host writes the code in a message or nobody
arrives.

⚠️ The noticeboard keeps one card per source. The second session of the same
survey therefore rewrites the card with today's code and lifts it back to the
top of the feed: a card still showing last month's code would be worse than no
card. The republication also clears the expiry date: otherwise a card that
expired last month and was republished this morning dropped out of the feed at
the next pass of the cron that expires posts, during the session.

⚠️ And one card per survey, across all companies: if the survey's card already
sits on ANOTHER company's noticeboard, the button refuses and says so, instead
of moving that card. An Odoo survey carries no company; the feed does.

⚠️ Two more refusals, which are Odoo's own and not ours: a session code leads
nowhere on a **certification**, nor on a survey restricted to **invited**
people.
""",
}
