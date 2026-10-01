"""Textes du tableau de bord livré, pour l'export des traductions.

bf_bi les traduit à la lecture, dans la langue de la personne qui regarde.
"""
from odoo.tools.translate import LazyTranslate

_lt = LazyTranslate(__name__)

TERMS = [
    _lt("Active services"),
    _lt("Active services per environment"),
    _lt("Active services per server and environment"),
    _lt("Average storage used (%)"),
    _lt("Backup success rate"),
    _lt("Backups per month and result"),
    _lt("Customer"),
    _lt("Domains expiring within 90 days"),
    _lt("Failed backups"),
    _lt("Period"),
    _lt("Services down"),
    _lt("vs matching period"),
]
