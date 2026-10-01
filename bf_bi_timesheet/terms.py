"""Textes du tableau de bord livré, pour l'export des traductions.

bf_bi les traduit à la lecture, dans la langue de la personne qui regarde.
"""
from odoo.tools.translate import LazyTranslate

_lt = LazyTranslate(__name__)

TERMS = [
    _lt("Customer"),
    _lt("Hours"),
    _lt("Hours per customer and month"),
    _lt("Hours per month and customer"),
    _lt("Labour cost"),
    _lt("Margin"),
    _lt("Margin rate"),
    _lt("Period"),
    _lt("Revenue"),
    _lt("Revenue per customer"),
    _lt("Revenue per month"),
    _lt("vs matching period"),
]
