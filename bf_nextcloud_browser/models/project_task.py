"""Resolve a task's Nextcloud folder from its project (with optional subfolder)."""

from odoo import api, fields, models


class ProjectTask(models.Model):
    _inherit = "project.task"

    nc_task_subfolder = fields.Char(
        string="Sous-dossier Nextcloud (tache)",
        help="Sous-chemin optionnel sous le dossier Nextcloud du projet, propre a "
        "cette tache (ex: 'Pieces jointes').",
    )
    nc_documents_config_id = fields.Many2one(
        "nextcloud.document.config",
        string="Config Nextcloud",
        compute="_compute_nc_documents",
        compute_sudo=False,
    )
    nc_documents_folder = fields.Char(
        string="Dossier Nextcloud",
        compute="_compute_nc_documents",
        compute_sudo=False,
    )

    @api.depends(
        "project_id",
        "project_id.nc_documents_config_id",
        "project_id.nc_documents_folder",
        "nc_task_subfolder",
    )
    def _compute_nc_documents(self):
        for task in self:
            project = task.project_id
            task.nc_documents_config_id = (
                project.nc_documents_config_id if project else False
            )
            folder = project.nc_documents_folder if project else False
            sub = (task.nc_task_subfolder or "").strip().strip("/")
            if folder and sub:
                folder = folder.rstrip("/") + "/" + sub
            task.nc_documents_folder = folder
