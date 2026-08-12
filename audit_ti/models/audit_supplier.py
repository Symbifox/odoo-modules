from odoo import fields, models


class AuditSupplier(models.Model):
    _name = "audit.supplier"
    _description = "Fournisseur TI"
    _order = "name"

    name = fields.Char(string="Nom", required=True)
    supplier_type = fields.Selection(
        [
            ("it_provider", "Prestataire TI"),
            ("saas", "Application SaaS"),
            ("cloud", "Cloud public"),
            ("backup", "Sauvegarde"),
            ("other", "Autre"),
        ],
        string="Type",
        default="other",
    )
    nc_status = fields.Selection(
        [
            ("completed", "Complété"),
            ("wip", "En cours (WIP)"),
            ("to_create", "À créer"),
            ("saas", "SaaS"),
            ("new", "Nouveau"),
            ("na", "N/A"),
        ],
        string="Statut NC",
        default="new",
    )
    nc_status_detail = fields.Char(string="Détail statut")
    active = fields.Boolean(string="Actif", default=True)
    alias_ids = fields.One2many(
        "audit.supplier.alias", "supplier_id", string="Alias"
    )
    client_supplier_ids = fields.One2many(
        "audit.client.supplier", "supplier_id", string="Relations clients"
    )
