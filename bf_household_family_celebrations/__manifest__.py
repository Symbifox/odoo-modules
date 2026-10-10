# -*- coding: utf-8 -*-
{
    "name": "Household Family: Celebrations",
    "version": "18.0.1.0.1",
    "category": "Productivity",
    "summary": "The birthdays of the household's children, in Celebrations and the calendar",
    "description": """
Bridge between Household Family (``bf_household_family``) and Celebrations
(``bf_celebrations``).

A child whose parents gave the birth day gets their birthday in Celebrations, like
a colleague's: an all-day entry in the calendar, and a reminder to the parent who
holds the child's records. A parent can turn it off for their child. The age is
never shown.

Labels are written in English; French (Canada) comes from ``i18n/fr_CA.po``.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "depends": ["bf_household_family", "bf_celebrations"],
    "data": [
        "views/household_child_views.xml",
    ],
    "icon": "/bf_household_family_celebrations/static/description/icon.png",
    "installable": True,
    "application": False,
    "auto_install": True,
}
