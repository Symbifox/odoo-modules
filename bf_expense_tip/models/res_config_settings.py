# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    expense_tip_account_id = fields.Many2one(
        related="company_id.expense_tip_account_id",
        string="Compte des pourboires",
        readonly=False,
    )
