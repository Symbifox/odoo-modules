# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Extension de project.project : compteur + smart button de licences."""
from odoo import api, fields, models


class ProjectProject(models.Model):
    _inherit = "project.project"

    license_seat_ids = fields.One2many(
        comodel_name="hosting.license.seat",
        inverse_name="project_id",
        string="License seats",
    )
    license_seat_count = fields.Integer(
        string="License count",
        compute="_compute_license_seat_count",
    )

    @api.depends("license_seat_ids")
    def _compute_license_seat_count(self):
        for proj in self:
            proj.license_seat_count = len(proj.license_seat_ids)

    def action_view_licenses(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Licenses — %s", self.name),
            "res_model": "hosting.license.seat",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("project_id", "=", self.id)],
            "context": {"default_project_id": self.id, "default_assignee_type": "project"},
        }
