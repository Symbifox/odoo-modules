from odoo import models


class ProjectProject(models.Model):
    _inherit = "project.project"

    def action_open_gantt(self):
        """Open the Gantt client action focused on this project."""
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "bf_progression_gantt",
            "name": "Échéancier Gantt",
            "params": {"project_id": self.id},
            "context": {"default_project_id": self.id},
        }
