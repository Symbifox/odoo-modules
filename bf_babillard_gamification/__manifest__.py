# -*- coding: utf-8 -*-
{
    "name": "Babillard : reconnaissance",
    "summary": "Un badge remis par un collègue se voit enfin",
    "version": "18.0.1.1.0",
    "category": "Human Resources",
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",
    "depends": ["bf_babillard", "hr_gamification"],
    "auto_install": True,
    "installable": True,
    "description": """
Pont Babillard ↔ Badges
=======================

S'auto-installe quand `bf_babillard` et les badges d'Odoo sont tous deux là.

Un badge remis par un collègue **ne se voit nulle part** dans Odoo : un courriel
part au seul destinataire, et le badge se range dans un onglet de sa fiche
d'employé. Une reconnaissance que personne ne croise n'en est pas une.

Ce pont pose la carte au babillard, avec le mot de la personne qui remet, s'il
y en a un.

Aucun pointage, aucun classement : une carte par badge, et rien qui
s'additionne.
""",
}
