# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class HostingServiceTag(models.Model):
    _name = "hosting.service.tag"
    _description = "Étiquette de service d'hébergement"
    _order = "name"

    name = fields.Char(
        string="Nom de l'étiquette",
        required=True,
        translate=True,
    )
    color = fields.Integer(
        string="Index de couleur",
    )
    active = fields.Boolean(
        string="Actif",
        default=True,
    )

    _sql_constraints = [
        ("name_uniq", "UNIQUE(name)", "Le nom de l'étiquette doit être unique !"),
    ]
