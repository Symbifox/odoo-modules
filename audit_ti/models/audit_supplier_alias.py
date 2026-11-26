from odoo import fields, models


class AuditSupplierAlias(models.Model):
    _name = "audit.supplier.alias"
    _description = "IT supplier alias"
    _order = "name"

    name = fields.Char(string="Alias", required=True)
    supplier_id = fields.Many2one(
        "audit.supplier", string="Supplier", required=True, ondelete="cascade"
    )
