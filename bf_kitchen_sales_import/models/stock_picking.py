from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class StockPicking(models.Model):
    _inherit = "stock.picking"

    bf_kitchen_source = fields.Selection(selection_add=[("import", "Imported sales")],
                                         ondelete={"import": "set null"})
    bf_kitchen_import_id = fields.Many2one(
        "bf.kitchen.sales.import", "Sales import", readonly=True, copy=False, index="btree_not_null")

    @api.model
    def _bf_kitchen_server_fields(self):
        return super()._bf_kitchen_server_fields() | {"bf_kitchen_import_id"}
