from odoo import fields, models


class KitchenStation(models.Model):
    """A kitchen screen: the hot line, the cold line, the bar. It shows the dishes of its register
    categories (all of them when none is set) sent from its registers."""
    _name = "bf.kitchen.station"
    _description = "Kitchen station"
    _order = "sequence, id"
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    pos_config_ids = fields.Many2many(
        "pos.config", "bf_kitchen_station_pos_config_rel", "station_id", "pos_config_id",
        string="Registers", check_company=True,
        domain="[('module_pos_restaurant', '=', True)]",
        help="The registers whose orders reach this station.")
    pos_categ_ids = fields.Many2many(
        "pos.category", "bf_kitchen_station_pos_category_rel", "station_id", "pos_categ_id",
        string="Register categories",
        help="The dishes of these categories reach this station. None: every dish.")

    def _bf_takes(self, product):
        """A dish of one of its categories or of their sub-categories (as Odoo's order printers do)."""
        self.ensure_one()
        if not self.pos_categ_ids:
            return True
        categories = product.pos_categ_ids
        while categories:
            if categories & self.pos_categ_ids:
                return True
            categories = categories.parent_id
        return False
