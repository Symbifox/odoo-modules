# -*- coding: utf-8 -*-
from odoo import api, fields, models


class RetailCredit(models.Model):
    """What came back to the supplier and was credited: bread, milk, magazines.

    It is kept apart from the cost series on purpose. A credit for what did not sell does
    not make the next unit cheaper, so folding it into the cost would flatter the margin of
    everything still on the shelf. What it does say is: order less of this.
    """
    _name = "bf.retail.credit"
    _description = "Retail credit for a return"
    _order = "date desc, id desc"
    _check_company_auto = True

    product_id = fields.Many2one(
        "product.product", "Item", required=True, index=True, ondelete="cascade",
        check_company=True)
    company_id = fields.Many2one(
        "res.company", "Company", required=True, index=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    partner_id = fields.Many2one("res.partner", "Supplier", index=True)
    date = fields.Date("On", required=True, index=True, default=fields.Date.context_today)
    qty = fields.Float("Quantity", digits="Product Unit of Measure", required=True)
    amount = fields.Monetary("Credited", currency_field="currency_id")
    move_line_id = fields.Many2one(
        "account.move.line", "Credit note line", ondelete="set null", index=True)
    move_id = fields.Many2one(related="move_line_id.move_id", string="Credit note", store=True)

    _sql_constraints = [
        ("one_per_move_line", "UNIQUE (move_line_id)",
         "This credit note line is already recorded."),
    ]

    @api.depends("product_id", "qty", "date")
    def _compute_display_name(self):
        for credit in self:
            credit.display_name = "%s, %s" % (
                credit.product_id.display_name or "", credit.date or "")
