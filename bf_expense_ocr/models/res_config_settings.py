# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    expense_ocr_auto = fields.Boolean(
        related="company_id.expense_ocr_auto",
        string="Lire les reçus au téléversement",
        readonly=False,
    )
