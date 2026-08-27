# -*- coding: utf-8 -*-
"""Les réglages généraux de l'atelier éditorial."""

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_editorial_require_all_langs = fields.Boolean(
        string="Exiger toutes les langues avant de considérer une entrée publiée",
        config_parameter="bf_editorial.require_all_langs",
        default=True,
        help="Un article dont la traduction n'est pas sortie reste incomplet"
             " et n'est pas compté dans la cadence. Chaque calendrier peut"
             " surcharger ce réglage.",
    )
    bf_editorial_source_recheck_months = fields.Integer(
        string="Revérifier les sources tous les (mois)",
        config_parameter="bf_editorial.source_recheck_months",
        default=3,
        help="Au-delà, une source est considérée périmée et remonte dans la"
             " garde de pré-vol.",
    )
