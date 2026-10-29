# -*- coding: utf-8 -*-
{
    "name": "Symbifox Noms d'usage et pronoms — pont CardDAV",
    # 18.0.1.0.0: premier jet.
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "summary": "Les pronoms et le genre grammatical dans les cartes de contacts servies en CardDAV",
    "description": """
Pont CardDAV
============

Les carnets d'adresses que ``bf_dav_server`` sert aux téléphones et aux
logiciels de courriel reçoivent, pour chaque personne qui les a donnés, ses
pronoms et son genre grammatical : propriétés ``PRONOUNS`` et ``GRAMGENDER``
de la RFC 9554 (registre IANA des éléments vCard). Le carnet sait alors
comment écrire à la personne, sans le deviner d'un prénom.

**En sortie seulement**. Les pronoms se
donnent dans Odoo, par la personne au portail ou par qui les tient d'elle. Ce
qu'un téléphone renvoie dans ces deux propriétés n'est pas importé, ni gardé
avec ses données propres : sinon la carte les porterait deux fois au prochain
aller-retour.

Les cartes personnelles d'un usager (son carnet à lui) ne sont pas touchées.

Les cartes servies restent en vCard 3.0 (``bf_dav_server``) : ``PRONOUNS`` et
``GRAMGENDER`` sont des propriétés de la vCard 4 (RFC 9554). Un client qui ne
les connaît pas les ignore. Changer les pronoms d'une fiche, comme poser le
pont, change l'ETag de sa carte et le signale aux téléphones en synchronisation.
""",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_preferred_name", "bf_dav_server"],
    "data": [],
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "auto_install": True,
    "application": False,
}
