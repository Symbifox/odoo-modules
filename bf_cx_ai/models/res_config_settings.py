"""Bridge setting: opt-in automatic AI analysis of verbatims.

Scalar field types only: Text/Html fields on res.config.settings crash
the whole Settings page.
"""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_cx_ai_auto_analyze = fields.Boolean(
        string="Analyse IA automatique des verbatims",
        config_parameter="bf_cx.ai_auto_analyze",
        help="Un traitement quotidien analyse (sentiment, thèmes, résumé) "
             "jusqu'à 20 commentaires récents non analysés, par le pont IA : "
             "le texte des commentaires est transmis au modèle d'IA. "
             "Désactivé par défaut.",
    )
