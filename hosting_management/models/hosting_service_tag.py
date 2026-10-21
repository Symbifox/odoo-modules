# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class HostingServiceTag(models.Model):
    _name = "hosting.service.tag"
    _description = "Hosting service tag"
    _order = "name"

    name = fields.Char(
        string="Tag name",
        required=True,
        translate=True,
    )
    color = fields.Integer(
        string="Color index",
    )
    active = fields.Boolean(
        string="Active",
        default=True,
    )

    _sql_constraints = [
        ("name_uniq", "UNIQUE(name)", "The tag name must be unique!"),
    ]
