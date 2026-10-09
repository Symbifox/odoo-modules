"""18.0.1.0.2 : le libellé de la date de début.

Il s'appelait « Start », comme le bouton de l'assistant, et prenait sa traduction
(« Commencer »). Le libellé source devient « Started at », mais une montée met à
jour l'anglais sans écraser une traduction déjà en base : on la pose ici.
"""
from odoo import SUPERUSER_ID, api

TRADUCTIONS = {"fr_CA": "Début", "fr_FR": "Début", "fr_BE": "Début", "fr_CH": "Début"}


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    champ = env["ir.model.fields"]._get("bf.impersonate.session", "date_start")
    actives = set(env["res.lang"].search([]).mapped("code"))
    valeurs = {code: texte for code, texte in TRADUCTIONS.items() if code in actives}
    if champ and valeurs:
        champ.update_field_translations("field_description", valeurs)
