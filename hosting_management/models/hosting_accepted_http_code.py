# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class HostingAcceptedHttpCode(models.Model):
    _name = "hosting.accepted.http.code"
    _description = "Code HTTP accepté"
    _order = "code"

    code = fields.Integer(string="Code HTTP", required=True)
    name = fields.Char(string="Description", required=True)

    _sql_constraints = [
        ("code_unique", "unique(code)", "Ce code HTTP existe déjà."),
    ]

    @api.depends("code", "name")
    def _compute_display_name(self):
        for record in self:
            record.display_name = f"{record.code} — {record.name}"
