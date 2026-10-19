# -*- coding: utf-8 -*-
"""Les réglages généraux de l'atelier éditorial."""

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    bf_editorial_require_all_langs = fields.Boolean(
        string="Require all languages before considering an entry published",
        config_parameter="bf_editorial.require_all_langs",
        default=True,
        help="An article whose translation has not come out stays "
             "incomplete and is not counted in the cadence. Each calendar "
             "can override this setting.",
    )
    bf_editorial_source_recheck_months = fields.Integer(
        string="Recheck sources every (months)",
        config_parameter="bf_editorial.source_recheck_months",
        default=3,
        help="Beyond this, a source is considered outdated and shows up "
             "in the preflight guard.",
    )
