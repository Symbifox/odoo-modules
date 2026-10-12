"""Resolve a task's Nextcloud folder from its project (with optional subfolder)."""

from odoo import api, fields, models

from .bf_nc_client_folder import clean_subfolder, joined_folder


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
    # Same value as nc_documents_folder, under the name every record the
    # browser serves carries since 18.0.4.2.0 (partner, project, task).
    nc_browser_folder = fields.Char(
        string="Dossier Nextcloud (navigateur)",
        compute="_compute_nc_documents",
        compute_sudo=False,
        groups="base.group_user,bf_nextcloud_browser.group_nc_browser_user",
    )

    @api.constrains("nc_task_subfolder")
    def _check_nc_task_subfolder(self):
        # Refused when typed, not only when the tab opens (18.0.4.2.0).
        for task in self:
            clean_subfolder(task.nc_task_subfolder)

    @api.depends(
        "project_id",
        "project_id.nc_documents_config_id",
        "project_id.nc_browser_folder",
        "nc_task_subfolder",
    )
    def _compute_nc_documents(self):
        for task in self:
            project = task.project_id
            task.nc_documents_config_id = (
                project.nc_documents_config_id if project else False
            )
            folder = joined_folder(
                project.nc_browser_folder if project else False, task.nc_task_subfolder
            )
            task.nc_documents_folder = folder
            task.nc_browser_folder = folder
