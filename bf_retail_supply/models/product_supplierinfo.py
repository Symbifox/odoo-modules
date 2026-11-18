# -*- coding: utf-8 -*-
from odoo import api, fields, models

MANAGER = "bf_retail.group_retail_manager"


class ProductSupplierinfo(models.Model):
    """The vendor price read per unit of the item itself.

    A price quoted by the dozen says nothing next to a shelf that sells by the unit, so the
    price is brought to the item's own unit of measure, computed and never stored.

    ⚠️ In Odoo 18 a vendor line does NOT carry its own unit: `product_uom` is a related
    field on `product_tmpl_id.uom_po_id`, shared by every vendor of the item. Two suppliers
    selling in different pack sizes are therefore modelled by their minimum quantities, not
    by a unit each. The category guard below stays as insurance; Odoo's own constraint
    already forbids a purchase unit from another category.
    """
    _inherit = "product.supplierinfo"

    bf_retail_price_per_unit = fields.Float(
        "Price per unit", digits=(16, 4), compute="_compute_bf_retail_per_unit",
        groups=MANAGER,
        help="The vendor price brought to the item's own unit of measure. Zero when the two "
             "units do not measure the same thing: nothing is guessed.")
    bf_retail_best = fields.Boolean(
        "Cheapest today", compute="_compute_bf_retail_best", groups=MANAGER,
        help="The lowest price per unit among the vendor lines of this item that are valid "
             "today in this company.")

    def _bf_retail_product(self):
        self.ensure_one()
        return self.product_id or self.product_tmpl_id.product_variant_ids[:1]

    @api.depends("price", "product_uom", "product_id", "product_tmpl_id")
    def _compute_bf_retail_per_unit(self):
        for info in self:
            product = info._bf_retail_product()
            uom = info.product_uom or (product.uom_id if product else False)
            if not product or not uom or uom.category_id != product.uom_id.category_id:
                info.bf_retail_price_per_unit = 0.0
                continue
            info.bf_retail_price_per_unit = uom._compute_price(info.price, product.uom_id)

    @api.depends("bf_retail_price_per_unit", "date_start", "date_end", "product_id",
                 "product_tmpl_id")
    @api.depends_context("company")
    def _compute_bf_retail_best(self):
        today = fields.Date.context_today(self)
        company = self.env.company
        Info = self.env["product.supplierinfo"].sudo()
        valid = ["|", ("date_start", "=", False), ("date_start", "<=", today),
                 "|", ("date_end", "=", False), ("date_end", ">=", today),
                 ("company_id", "in", (company.id, False))]
        for info in self:
            product = info._bf_retail_product()
            mine = info.bf_retail_price_per_unit
            if not product or not mine:
                info.bf_retail_best = False
                continue
            others = Info.search(valid + [
                "|", ("product_id", "=", product.id),
                "&", ("product_id", "=", False),
                ("product_tmpl_id", "=", product.product_tmpl_id.id)])
            prices = [o.bf_retail_price_per_unit for o in others
                      if o.bf_retail_price_per_unit]
            info.bf_retail_best = bool(prices) and mine <= min(prices) + 1e-9
