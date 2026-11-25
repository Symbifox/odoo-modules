from odoo import fields, models


class ProjectProject(models.Model):
    _inherit = "project.project"

    federation_peer_ids = fields.Many2many(
        "federation.peer", "project_federation_peer_rel", "project_id", "peer_id",
        string="Federation peers", domain="[('state', 'in', ('active', 'invited', 'draft'))]",
        help="Only this project's tasks can be federated, and only with "
             "these peers. Empty: no task in the project offers "
             "federation.")
    federation_enabled = fields.Boolean(compute="_compute_federation_enabled", string="Federation "
                                                                                      "possible")

    def _compute_federation_enabled(self):
        for project in self:
            project.federation_enabled = bool(project.federation_peer_ids)
