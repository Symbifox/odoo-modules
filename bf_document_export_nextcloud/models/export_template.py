import posixpath

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.bf_document_nextcloud_sync.models.nextcloud_document_config import _sanitize_nc_path


def nc_normalize(path):
    """« Politiques/x/../y/ » → « /Politiques/y »: the form paths are compared in."""
    return posixpath.normpath("/" + (path or "").strip().lstrip("/"))


def nc_under(path, folder):
    path, folder = nc_normalize(path), nc_normalize(folder)
    return folder == "/" or path == folder or path.startswith(folder + "/")


class BfDocumentExportTemplate(models.Model):
    _inherit = "bf.document.export.template"

    nc_config_id = fields.Many2one(
        "nextcloud.document.config",
        string="Nextcloud",
        help="Each full export of this template is deposited on this Nextcloud, "
             "with its service account.",
    )
    nc_target_path = fields.Char(
        string="Deposit folder",
        help="Folder on Nextcloud that receives the published copy, for example "
             "« /Politiques et procédures (copie publiée) ». Keep it apart from the "
             "folders where the source files live: the export refuses to deposit "
             "over them. Share it read-only with the staff.",
    )
    nc_source_config_id = fields.Many2one(
        "nextcloud.document.config",
        string="Source Nextcloud",
        help="The Nextcloud the export reads source files from, with its service account. "
             "A document linked to another configuration is left out and reported.",
    )
    nc_source_folders = fields.Text(
        string="Source folders",
        help="Nextcloud folders the export may read source files from, one per line, "
             "for example « /Entreprise/Politiques ». A document whose file is "
             "elsewhere is left out and reported: the files are read with the "
             "configuration's service account, whoever linked them.",
    )
    nc_file_ids = fields.One2many("bf.document.export.nc.file", "template_id", string="Deposited files")
    nc_file_count = fields.Integer(
        compute="_compute_nc_file_count", string="Files on Nextcloud", compute_sudo=True
    )

    @api.depends("nc_file_ids.state")
    def _compute_nc_file_count(self):
        for template in self:
            template.nc_file_count = len(template.nc_file_ids.filtered(lambda f: f.state == "present"))

    def _bf_nc_source_folders(self):
        self.ensure_one()
        return [nc_normalize(line) for line in (self.nc_source_folders or "").splitlines() if line.strip()]

    def _bf_export_cache(self, reader):
        cache = super()._bf_export_cache(reader)
        cache["nc_source_config"] = self.nc_source_config_id.sudo()
        cache["nc_source_folders"] = self._bf_nc_source_folders()
        return cache

    @api.constrains("nc_config_id", "nc_target_path", "nc_source_folders", "nc_source_config_id", "user_id")
    def _check_nc_target(self):
        for template in self:
            if template.nc_config_id and not (template.nc_target_path or "").strip("/ "):
                raise ValidationError(self.env._(
                    "Choose a deposit folder: the export never deposits at the root of Nextcloud."))
            if template.nc_config_id and not template.user_id:
                raise ValidationError(self.env._(
                    "A template that deposits on Nextcloud needs an « Export as » person: only "
                    "an export run as that person replaces the published copy."))
            for folder in template._bf_nc_source_folders():
                _sanitize_nc_path(folder)
                if folder == "/":
                    raise ValidationError(self.env._(
                        "A source folder cannot be the root of Nextcloud: name the folders where "
                        "the registry's files live."))
            if template.nc_target_path:
                _sanitize_nc_path(template.nc_target_path)
                target = nc_normalize(template.nc_target_path)
                for folder in template._bf_nc_source_folders():
                    if nc_under(target, folder) or nc_under(folder, target):
                        raise ValidationError(self.env._(
                            "The deposit folder %(target)s and the source folder %(folder)s overlap: "
                            "the published copy must never be written among its sources.",
                            target=target, folder=folder))

    def action_view_nc_files(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Files on Nextcloud"),
            "res_model": "bf.document.export.nc.file",
            "view_mode": "list",
            "domain": [("template_id", "=", self.id)],
            "context": {"search_default_present": 1},
        }
