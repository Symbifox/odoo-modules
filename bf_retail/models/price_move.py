# -*- coding: utf-8 -*-
from odoo import api, fields, models

SOURCES = [
    ("batch", "Price change batch"),
    ("hand", "Written on the product"),
]


class RetailPriceMove(models.Model):
    """One move of the shelf price of a followed product: what it was, what it became, when.

    🔴 The index of cost against price needs the shelf price of a past day, and it used to read
    the batches the module had applied itself. A price a manager typed on the product was
    invisible to it: the store had passed the increase, and the index said it had passed
    nothing. Every move is written here, from a batch or by hand, and the index reads the
    journal.

    Nothing is written by hand in this model: the two doors a shelf price goes through write
    it, under sudo, because a price can be moved by a purchase or an inventory manager who is
    not in the retail group at all. There is no check_company either -- a journal has to hold
    what happened, not refuse it.
    """
    _name = "bf.retail.price.move"
    _description = "Retail shelf price move"
    _order = "date desc, id desc"

    product_id = fields.Many2one(
        "product.product", "Product", required=True, index=True, ondelete="cascade")
    company_id = fields.Many2one("res.company", "Company", required=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id", string="Currency")
    date = fields.Date("On", required=True, index=True)
    price_old = fields.Monetary("Price before", currency_field="currency_id")
    price_new = fields.Monetary("Price after", currency_field="currency_id")
    source = fields.Selection(SOURCES, "From", required=True, default="hand")
    change_line_id = fields.Many2one(
        "bf.retail.price.change.line", "Batch line", ondelete="set null", index=True)
    change_id = fields.Many2one(related="change_line_id.change_id", string="Batch", store=True)
    user_id = fields.Many2one("res.users", "Written by")

    @api.depends("product_id", "date")
    def _compute_display_name(self):
        for move in self:
            move.display_name = "%s, %s" % (move.product_id.display_name or "", move.date or "")
