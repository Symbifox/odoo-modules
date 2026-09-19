# -*- coding: utf-8 -*-
{
    "name": "Symbifox — Agenda mobile",
    "summary": "Agenda et échéances pour l'app mobile, avec la couche Symbifox "
               "(OdJ, compte rendu, report de rappel)",
    "version": "18.0.3.2.0",
    "category": "Productivity",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "LGPL-3",
    "installable": True,
    # `bf_email_management` porte les deux verbes du rappel (`bf_snooze`,
    # `bf_dismiss`) et l'accusé durable qui donne une clé stable à une
    # occurrence. Sans lui il n'y a pas d'aller-retour possible, donc la
    # dépendance est réelle et pas un raccourci.
    # `bf_meeting` n'est PAS requis : ses pastilles (`bf_agenda_state`,
    # `bf_minutes_state`) sont lues quand le champ existe, et l'app cache la
    # section quand `features.meetings` est faux.
    "depends": ["calendar", "project", "bf_email_management"],
    "data": [],
}
