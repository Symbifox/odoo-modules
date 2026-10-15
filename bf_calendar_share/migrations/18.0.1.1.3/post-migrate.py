# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Recharge les traductions du module en écrasant les anciennes.

Une montée garde les traductions déjà en base : la fiche d'un lien continuait de
dire, en français, que le journal est gardé 90 jours (il l'est un an par défaut).
"""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    langues = [code for code, _nom in env["res.lang"].get_installed() if code != "en_US"]
    if langues:
        env["ir.module.module"]._load_module_terms(["bf_calendar_share"], langues, overwrite=True)
