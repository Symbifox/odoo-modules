from odoo import _, api, models

from odoo.addons.bf_kitchen.models.kitchen_consumption import SALE_TYPES


class KitchenMenu(models.TransientModel):
    _inherit = "bf.kitchen.menu"

    @api.model
    def _bf_menu_section(self, product):
        """A dish sold at the register belongs to its register category (Starters, Mains, Desserts)."""
        categ = product.pos_categ_ids.sorted(lambda c: (c.sequence, c.id))[:1]
        return categ.name if categ else super()._bf_menu_section(product)

    @api.model
    def _bf_menu_sales(self, company, start, end, warehouse=None):
        """The register's sales: lines of the paid orders, refunds deducted, before taxes; with a warehouse,
        the registers whose orders leave from it."""
        sales = super()._bf_menu_sales(company, start, end, warehouse=warehouse)
        domain = [
            ("order_id.company_id", "=", company.id), ("order_id.state", "in", ("paid", "done", "invoiced")),
            ("order_id.date_order", ">=", start), ("order_id.date_order", "<", end),
            ("product_id.bf_kitchen_type", "in", SALE_TYPES)]
        if warehouse:
            domain.append(("order_id.config_id.picking_type_id.warehouse_id", "=", warehouse.id))
        label = _("Register")
        for line in self.env["pos.order.line"].sudo().search(domain):
            self._bf_menu_add(sales, line.product_id, line.qty,
                              line.order_id.currency_id._convert(line.price_subtotal, company.currency_id, company,
                                                                 line.order_id.date_order.date()), label)
        return sales
