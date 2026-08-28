import base64
import logging
import mimetypes
import posixpath
from email.utils import parsedate_to_datetime

from markupsafe import escape as html_escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.nextcloud_document_config import _sanitize_nc_path

_logger = logging.getLogger(__name__)


class DocumentNcUploadWizard(models.TransientModel):
    """Wizard to upload a new document version to Nextcloud."""

    _name = "document.nc.upload.wizard"
    _description = "Televerser une version de document sur Nextcloud"

    document_id = fields.Many2one(
        "project.document",
        string="Document",
        required=True,
        readonly=True,
    )
    nc_config_id = fields.Many2one(
        "nextcloud.document.config",
        string="Configuration Nextcloud",
        required=True,
    )
    nc_file_path = fields.Char(
        string="Chemin de destination",
        required=True,
        help="Chemin complet du fichier sur Nextcloud",
    )
    file_data = fields.Binary(
        string="Fichier",
        required=True,
        help="Fichier a televerser sur Nextcloud",
    )
    file_name = fields.Char(
        string="Nom du fichier",
    )
    version_number = fields.Char(
        string="Numero de version",
        help="Ex: 1.0, 2.1, etc.",
    )
    change_type = fields.Selection(
        [
            ("major", "Majeure"),
            ("minor", "Mineure"),
            ("patch", "Correctif"),
            ("editorial", "Editoriale"),
        ],
        string="Type de modification",
        default="minor",
    )
    change_summary = fields.Text(
        string="Resume des modifications",
    )
    create_version_record = fields.Boolean(
        string="Creer un enregistrement de version",
        default=True,
        help="Creer automatiquement un enregistrement project.document.version",
    )

    @api.constrains("nc_file_path")
    def _check_path(self):
        for record in self:
            if record.nc_file_path:
                _sanitize_nc_path(record.nc_file_path)

    @api.onchange("file_name")
    def _onchange_file_name(self):
        """Update destination path when file name changes."""
        if self.file_name and self.nc_file_path:
            folder = posixpath.dirname(self.nc_file_path)
            if folder and not folder.endswith("/"):
                folder += "/"
            # Only update if current path ends with / (folder only)
            if self.nc_file_path.endswith("/"):
                self.nc_file_path = folder + self.file_name

    def action_upload(self):
        """Upload file to Nextcloud and optionally create version record."""
        self.ensure_one()

        if not self.file_data:
            raise UserError(_("Veuillez selectionner un fichier."))

        config = self.nc_config_id
        path = _sanitize_nc_path(self.nc_file_path)

        # Ensure the file name is in the path
        if path.endswith("/") and self.file_name:
            path = path + self.file_name

        # Detect MIME type
        content_type = "application/octet-stream"
        if self.file_name:
            # ⚠️ Surtout pas `guessed, _ = ...` : `_` est la fonction de
            # traduction d'Odoo, et l'affecter ici en ferait une locale pour
            # TOUTE la méthode. Le `_("Veuillez sélectionner un fichier.")`
            # plus haut lèverait alors un UnboundLocalError au lieu du
            # UserError attendu, donc une trace au lieu d'un message.
            guessed = mimetypes.guess_type(self.file_name)[0]
            if guessed:
                content_type = guessed

        # Decode base64 file data
        file_content = base64.b64decode(self.file_data)

        # Ensure parent directory exists
        parent_dir = posixpath.dirname(path)
        if parent_dir and parent_dir != "/":
            try:
                config._webdav_mkcol(parent_dir)
            except Exception:
                pass  # Directory may already exist

        # Upload via WebDAV PUT
        config._webdav_put(path, file_content, content_type)

        # Update document record
        doc = self.document_id
        vals = {
            "nc_file_path": path,
            "nc_config_id": config.id,
            "nc_content_type": content_type,
            "nc_file_size": len(file_content),
        }

        # Refresh metadata from NC (get file_id, etag, etc.)
        try:
            results = config._webdav_propfind(path, depth="0")
            if results:
                entry = results[0]
                if entry.get("etag"):
                    vals["nc_etag"] = entry["etag"]
                if entry.get("file_id"):
                    vals["nc_file_id"] = entry["file_id"]
                if entry.get("last_modified"):
                    try:
                        dt = parsedate_to_datetime(entry["last_modified"])
                        vals["nc_last_modified"] = fields.Datetime.to_string(dt)
                    except Exception:
                        pass
        except Exception as e:
            _logger.warning("Could not refresh NC metadata after upload: %s", e)

        doc.write(vals)

        # Optionally create version record
        if self.create_version_record:
            version_vals = {
                "document_id": doc.id,
                "change_type": self.change_type or "minor",
                "change_summary": self.change_summary or "",
                "author_id": self.env.user.id,
                "release_date": fields.Date.today(),
            }
            if self.version_number:
                version_vals["version_number"] = self.version_number

            # Attach the file as ir.attachment
            attachment = self.env["ir.attachment"].create({
                "name": self.file_name or posixpath.basename(path),
                "datas": self.file_data,
                "res_model": "project.document.version",
            })
            version_vals["attachment_id"] = attachment.id

            self.env["project.document.version"].create(version_vals)

        # Post to document chatter
        doc.message_post(
            body=_(
                "<p><strong>Nouvelle version televersee sur Nextcloud</strong></p>"
                "<p>Fichier: %s</p>"
                "<p>Taille: %s octets</p>"
            ) % (html_escape(posixpath.basename(path)), len(file_content)),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Televersement reussi"),
                "message": _("Fichier televerser sur Nextcloud: %s") % path,
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
