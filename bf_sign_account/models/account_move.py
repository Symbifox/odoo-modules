from odoo import models


class AccountMove(models.Model):
    _name = "account.move"
    _inherit = ["account.move", "bf.sign.mixin"]

    def _sign_report_ref(self):
        # Standard customer invoice / vendor bill PDF.
        return "account.account_invoices"
