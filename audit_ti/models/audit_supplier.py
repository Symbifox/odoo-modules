from odoo import fields, models


class AuditSupplier(models.Model):
    _name = "audit.supplier"
    _description = "IT supplier"
    _order = "name"

    name = fields.Char(string="Name", required=True)
    supplier_type = fields.Selection(
        [
            ("it_provider", "IT provider"),
            ("saas", "SaaS application"),
            ("cloud", "Public cloud"),
            ("backup", "Backup"),
            ("other", "Other"),
        ],
        string="Type",
        default="other",
    )
    nc_status = fields.Selection(
        [
            ("completed", "Completed"),
            ("wip", "In progress (WIP)"),
            ("to_create", "To create"),
            ("saas", "SaaS"),
            ("new", "New"),
            ("na", "N/A"),
        ],
        string="NC status",
        default="new",
    )
    nc_status_detail = fields.Char(string="Status detail")
    active = fields.Boolean(string="Active", default=True)
    alias_ids = fields.One2many(
        "audit.supplier.alias", "supplier_id", string="Alias"
    )
    client_supplier_ids = fields.One2many(
        "audit.client.supplier", "supplier_id", string="Client relationships"
    )
