"""Textes du tableau de bord livré, pour l'export des traductions.

bf_bi les traduit à la lecture, dans la langue de la personne qui regarde.
"""
from odoo.tools.translate import LazyTranslate

_lt = LazyTranslate(__name__)

TERMS = [
    _lt("Adjustments per month"),
    _lt("Customer"),
    _lt("Hour bank adjustments"),
    _lt("Hour bank balances"),
    _lt("Period"),
    _lt("vs matching period"),
]
