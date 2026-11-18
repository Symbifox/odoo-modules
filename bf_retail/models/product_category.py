# -*- coding: utf-8 -*-
from odoo import api, fields, models

from .res_company import ROUNDINGS


class ProductCategory(models.Model):
    _inherit = "product.category"

    bf_retail_managed = fields.Boolean(
        "Retail category", default=False,
        help="The products of this category, and of the categories below it, are followed at "
             "retail: their cost is kept as a series and their price answers to it.")
    bf_retail_margin_target = fields.Float(
        "Target margin (%)", digits=(16, 1),
        help="Empty or zero: the company's target is used. A category below this one with no "
             "target of its own uses this one.")
    bf_retail_price_rounding = fields.Selection(
        ROUNDINGS, "Price rounding",
        help="Empty: the category above, else the company. Bread and milk are often priced to "
             "the cent while confectionery ends in 0.99.")

    def _bf_retail_managed_here(self):
        """True when this category, or one above it, is followed at retail."""
        self.ensure_one()
        node = self
        while node:
            if node.bf_retail_managed:
                return True
            node = node.parent_id
        return False

    def _bf_retail_target(self, company):
        """The target margin as a share of 1, from this category, else the one above, else the
        company. A target of 0 means "not set", not "sell at cost"."""
        self.ensure_one()
        node = self
        while node:
            if node.bf_retail_margin_target:
                return node.bf_retail_margin_target / 100.0
            node = node.parent_id
        return (company.bf_retail_margin_target or 0.0) / 100.0

    def _bf_retail_rounding(self, company):
        self.ensure_one()
        node = self
        while node:
            if node.bf_retail_price_rounding:
                return node.bf_retail_price_rounding
            node = node.parent_id
        return company.bf_retail_price_rounding or "none"

    def write(self, vals):
        """A category that starts or stops being followed, or that is moved under another one,
        changes the stored flag of the products BELOW it too. Odoo's dependency on
        `categ_id.bf_retail_managed` only reaches the product's own category."""
        res = super().write(vals)
        touched = {"bf_retail_managed", "parent_id", "bf_retail_margin_target",
                   "bf_retail_price_rounding"} & set(vals)
        if not touched:
            return res
        below = self.search([("id", "child_of", self.ids)])
        # 🔴 `active_test=False`: Odoo's own trigger on `categ_id.bf_retail_managed` reaches the
        # archived products, this search did not. An archived product kept a flag its category
        # had dropped, and the stale flag waited for the day someone un-archived it.
        products = self.env["product.product"].sudo().with_context(active_test=False).search(
            [("categ_id", "in", below.ids)])
        if not products:
            return res
        if {"bf_retail_managed", "parent_id"} & touched:
            self.env.add_to_compute(products._fields["bf_retail_managed"], products)
            self.env.add_to_compute(
                products.product_tmpl_id._fields["bf_retail_managed"], products.product_tmpl_id)
        # The target and the rounding are read through the chain of parents, which Odoo's own
        # dependency on `categ_id.<field>` does not reach: the cached prices of the products
        # below are dropped by hand.
        unstored = ["bf_retail_target_pct", "bf_retail_price_suggested", "bf_retail_price_gap",
                    "bf_retail_margin_pct"]
        products.invalidate_recordset(unstored + ["bf_retail_price_untaxed"])
        products.product_tmpl_id.invalidate_recordset(unstored)
        return res
