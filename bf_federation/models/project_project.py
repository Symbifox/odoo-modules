from odoo import fields, models


class ProjectProject(models.Model):
    _inherit = "project.project"

    federation_peer_ids = fields.Many2many(
        "federation.peer", "project_federation_peer_rel", "project_id", "peer_id",
        string="Pairs de fédération", domain="[('state', 'in', ('active', 'invited', 'draft'))]",
        help="Seules les tâches de ce projet peuvent être fédérées, et seulement avec ces pairs. "
             "Vide : aucune tâche du projet ne propose la fédération.")
    federation_enabled = fields.Boolean(compute="_compute_federation_enabled", string="Fédération possible")

    def _compute_federation_enabled(self):
        for project in self:
            project.federation_enabled = bool(project.federation_peer_ids)
