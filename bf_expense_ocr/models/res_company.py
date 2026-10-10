# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    expense_ocr_auto = fields.Boolean(
        string="Read receipts on upload",
        default=False,
        help="Off, the receipt is read only when someone presses the "
             "button. On, every photo attached to an expense is sent to "
             "the provider configured in the LLM gateway as soon as it is "
             "uploaded.",
    )
