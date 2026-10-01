"""Textes du tableau de bord livré, pour l'export des traductions.

bf_bi les traduit à la lecture, dans la langue de la personne qui regarde.
"""
from odoo.tools.translate import LazyTranslate

_lt = LazyTranslate(__name__)

TERMS = [
    _lt("Average resolution time (days)"),
    _lt("Complaints per severity"),
    _lt("Complaints per severity and state"),
    _lt("Customer"),
    _lt("Detractors"),
    _lt("NPS"),
    _lt("NPS responses"),
    _lt("NPS responses per month"),
    _lt("Open complaints"),
    _lt("Period"),
    _lt("Promoters"),
    _lt("vs matching period"),
]
