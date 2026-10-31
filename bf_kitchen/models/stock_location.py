from odoo import api, fields, models

# The kitchen storage locations created under the stock of each warehouse, in the order a cook
# walks through them when counting.
# A location name is not a translated field: it is written once, in the company's language.
KITCHEN_LOCATIONS = [
    ("walk_in", "Walk-in cooler", "Chambre froide", 10),
    ("freezer", "Freezer", "Congélateur", 20),
    ("dry", "Dry storage", "Réserve sèche", 30),
    ("bar", "Bar", "Bar", 40),
]


class StockLocation(models.Model):
    _inherit = "stock.location"

    bf_kitchen_count_sequence = fields.Integer(
        "Count order", default=100,
        help="Order of this location in a kitchen count: the order a cook walks through them.")
    bf_kitchen_key = fields.Char(readonly=True, copy=False, index=True,
                                 help="Which default kitchen location this is, if any.")


class StockWarehouse(models.Model):
    _inherit = "stock.warehouse"

    def _bf_kitchen_create_locations(self):
        """Create the missing default kitchen locations under the stock of these warehouses.
        Idempotent: a location already made (even renamed or archived) is not made again."""
        Location = self.env["stock.location"].sudo().with_context(active_test=False)
        created = Location
        for warehouse in self.sudo():
            root = warehouse.lot_stock_id
            lang = warehouse.company_id.partner_id.lang or self.env.lang or "en_US"
            for key, name_en, name_fr, sequence in KITCHEN_LOCATIONS:
                if Location.search_count([("warehouse_id", "=", warehouse.id), ("bf_kitchen_key", "=", key)]):
                    continue
                created |= Location.create({
                    "name": name_fr if lang.startswith("fr") else name_en, "location_id": root.id, "usage": "internal",
                    "company_id": warehouse.company_id.id, "bf_kitchen_key": key,
                    "bf_kitchen_count_sequence": sequence})
        return created

    @api.model
    def _bf_kitchen_create_all_locations(self):
        """For the companies that cook: those with a kitchen recipe. A company that is not a kitchen
        does not want a walk-in cooler; the Settings button makes them on demand."""
        boms = self.env["mrp.bom"].sudo().with_context(active_test=False).search([
            ("product_tmpl_id.bf_kitchen_type", "in", ("preparation", "dish")), ("company_id", "!=", False)])
        return self.sudo().search([("company_id", "in", boms.company_id.ids)])._bf_kitchen_create_locations()


class ProductTemplate(models.Model):
    _inherit = "product.template"

    bf_kitchen_location_id = fields.Many2one(
        "stock.location", "Kitchen storage", company_dependent=True, check_company=True,
        domain="[('usage', '=', 'internal')]",
        help="Where this item is kept. A count lists it there even when its stock is zero.")
