# -*- coding: utf-8 -*-
{
    "name": "BF Calendar — the address comes from the address book",
    "summary": "A meeting with a customer gets the customer's address as its "
               "place, taken from the address book, on one line a phone can "
               "turn into directions. Suggested, never imposed: the picker "
               "lists the addresses already on file, and a place typed or "
               "picked by hand is never overwritten.",
    # 18.0.1.0.0 : premier jet. Odoo ne remplit ni ne suggère le lieu d'une
    #   rencontre : l'adresse se copiait du carnet vers l'agenda à la main,
    #   une visite à la fois, et la confirmation finissait par partir d'un
    #   autre agenda que celui où le travail se fait. Le lieu se remplit
    #   maintenant tout seul à l'arrivée d'un invité qui porte une adresse,
    #   en une seule ligne (« 128 rue des Forges, Sherbrooke QC J1H 4M2 »)
    #   pour que le téléphone la reconnaisse et ouvre le GPS, et un menu
    #   offre les autres adresses du carnet, pour l'usine qui n'est pas le
    #   siège social. Aucune source hors du carnet : rien n'est deviné depuis
    #   un courriel ni cherché sur le web.
    "version": "18.0.1.0.0",
    "description": """
The address comes from the address book
=======================================

A meeting with a customer gets the customer's address as its place, on one line
a phone can turn into directions, and a picker offers the other addresses on
file — the head office, or the plant that is not the head office.

Nothing is imposed: a place typed by hand, picked from the list, or carried by
a booking type is never replaced. Nothing is invented: every address comes from
the address book, never from an email domain or a website. And an online
meeting, which is not somewhere you drive to, is left alone.
""",
    "category": "Productivity",
    "website": "https://symbifox.com",
    "author": "Les services de consultation Blue Fox, Inc.",
    "license": "LGPL-3",
    "depends": ["calendar"],
    "data": [
        "views/calendar_event_views.xml",
    ],
    "installable": True,
}
