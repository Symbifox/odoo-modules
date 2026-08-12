from odoo import fields, models


class AuditSupplierAlias(models.Model):
    _name = "audit.supplier.alias"
    _description = "Alias fournisseur TI"
    _order = "name"

    name = fields.Char(string="Alias", required=True)
    supplier_id = fields.Many2one(
        "audit.supplier", string="Fournisseur", required=True, ondelete="cascade"
    )
