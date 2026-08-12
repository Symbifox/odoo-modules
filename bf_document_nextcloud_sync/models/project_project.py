from odoo import api, fields, models

from .nextcloud_document_config import _sanitize_nc_path


class ProjectProject(models.Model):
    """Extend project.project with Nextcloud document folder mapping."""

    _inherit = "project.project"

    nc_documents_config_id = fields.Many2one(
        "nextcloud.document.config",
        string="Config Nextcloud Documents",
        help="Configuration Nextcloud par defaut pour les documents de ce projet",
    )
    nc_documents_folder = fields.Char(
        string="Dossier Nextcloud",
        help="Chemin du dossier documents sur Nextcloud "
        "(ex: /Blue Fox/Documents/). Les nouveaux documents "
        "utiliseront ce chemin comme prefixe.",
    )

    @api.constrains("nc_documents_folder")
    def _check_nc_documents_folder(self):
        for record in self:
            if record.nc_documents_folder:
                _sanitize_nc_path(record.nc_documents_folder)
