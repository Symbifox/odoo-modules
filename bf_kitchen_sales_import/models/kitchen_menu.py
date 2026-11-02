from collections import defaultdict

from odoo import _, api, models

from odoo.addons.bf_kitchen.models.kitchen_consumption import SALE_TYPES


class KitchenMenu(models.TransientModel):
    _inherit = "bf.kitchen.menu"

    @api.model
    def _bf_menu_sales(self, company, start, end, warehouse=None):
        """The sales of another register, imported: quantities only, counted at the item's selling price. As
        the import, an item's day is netted, and a day below zero (refunds) is no sale.

        By day of sale for the menu. With a warehouse (the food cost of a period between two counts): the days
        whose food left that warehouse in the period, as the stock moves are read."""
        sales = super()._bf_menu_sales(company, start, end, warehouse=warehouse)
        domain = [("company_id", "=", company.id), ("import_id.state", "=", "done"), ("status", "=", "matched"),
                  ("product_id.bf_kitchen_type", "in", SALE_TYPES)]
        if warehouse:
            pickings = self.env["stock.picking"].sudo().search([
                ("bf_kitchen_import_id", "!=", False), ("state", "=", "done"),
                ("date_done", ">", start), ("date_done", "<=", end),
                ("picking_type_id.warehouse_id", "=", warehouse.id), ("location_dest_id.usage", "=", "customer")])
            days = {(p.bf_kitchen_import_id.id, p.bf_kitchen_event_date) for p in pickings}
            if not days:
                return sales
            domain.append(("import_id", "in", list({i for i, _d in days})))
            lines = self.env["bf.kitchen.sales.import.line"].sudo().search(domain).filtered(
                lambda l: (l.import_id.id, l.date) in days)
        else:
            first, last = self._bf_local_days(company, start, end)
            lines = self.env["bf.kitchen.sales.import.line"].sudo().search(
                domain + [("date", ">=", first), ("date", "<=", last)])
        days_qty = defaultdict(float)
        names = {}
        for line in lines:
            days_qty[(line.product_id.id, line.date)] += line.qty
            names[line.product_id.id] = line.source_id.name or _("Another register")
        Product = self.env["product.product"].sudo().with_company(company)
        for (product_id, _day), qty in days_qty.items():
            if qty <= 0:
                continue
            product = Product.browse(product_id)
            self._bf_menu_add(sales, product, qty, qty * product.lst_price, names[product_id], estimated=True)
        return sales
