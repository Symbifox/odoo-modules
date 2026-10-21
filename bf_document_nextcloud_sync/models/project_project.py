from odoo import api, fields, models

from .nextcloud_document_config import _sanitize_nc_path


class ProjectProject(models.Model):
    """Extend project.project with Nextcloud document folder mapping."""

    _inherit = "project.project"

    nc_documents_config_id = fields.Many2one(
        "nextcloud.document.config",
        string="Nextcloud documents config",
        help="Default Nextcloud configuration for this project's documents",
    )
    nc_documents_folder = fields.Char(
        string="Nextcloud folder",
        help="Path of the documents folder on Nextcloud (e.g. /Blue "
             "Fox/Documents/). New documents will use this path as a "
             "prefix.",
    )

    @api.constrains("nc_documents_folder")
    def _check_nc_documents_folder(self):
        for record in self:
            if record.nc_documents_folder:
                _sanitize_nc_path(record.nc_documents_folder)
