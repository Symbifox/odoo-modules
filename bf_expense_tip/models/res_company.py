# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    expense_tip_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Compte des pourboires",
        domain="[('deprecated', '=', False)]",
        help="Laissé vide, le pourboire va au même compte que la dépense qui "
             "le porte — il fait partie des frais de représentation. Le "
             "désigner ici sert à l'isoler pour l'analyse.",
    )
