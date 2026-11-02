from odoo import _, api, models


class KitchenMenu(models.TransientModel):
    _inherit = "bf.kitchen.menu"

    @api.model
    def _bf_menu_sales(self, company, start, end, warehouse=None):
        """The school meals ordered, at the price paid, for a meal linked to a kitchen dish, from the day it was
        linked (the meals before left nothing in the kitchen's stock). A meal cancelled or credited (school
        closed) is not a sale.

        By day of the meal for the menu. With a warehouse (the food cost of a period between two counts): by
        the moment the day's meals left that warehouse, as the stock moves are read."""
        sales = super()._bf_menu_sales(company, start, end, warehouse=warehouse)
        domain = [("company_id", "=", company.id), ("state", "=", "ordered"),
                  ("item_id.bf_kitchen_dish_id", "!=", False)]
        if warehouse:
            domain += [("bf_kitchen_picking_id.date_done", ">", start), ("bf_kitchen_picking_id.date_done", "<=", end),
                       ("bf_kitchen_picking_id.picking_type_id.warehouse_id", "=", warehouse.id)]
        else:
            first, last = self._bf_local_days(company, start, end)
            domain += [("date", ">=", first), ("date", "<=", last)]
        label = _("School meals")
        for order in self.env["bf.school.meal.order"].sudo().search(domain):
            item = order.item_id
            if item.bf_kitchen_dish_date and order.date < item.bf_kitchen_dish_date:
                continue
            self._bf_menu_add(sales, item.bf_kitchen_dish_id, 1.0, order.price, label)
        return sales
