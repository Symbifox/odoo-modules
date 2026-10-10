# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    expense_tip_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Tip account",
        domain="[('deprecated', '=', False)]",
        help="Left empty, the tip goes to the same account as the expense "
             "that carries it, as part of entertainment expenses. Setting "
             "it here isolates it for analysis.",
    )
