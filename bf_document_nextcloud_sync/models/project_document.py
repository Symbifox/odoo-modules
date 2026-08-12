import logging
import posixpath
from email.utils import parsedate_to_datetime
from markupsafe import escape as html_escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .nextcloud_document_config import _sanitize_nc_path, _validate_path_under_prefix

_logger = logging.getLogger(__name__)


class ProjectDocument(models.Model):
    """Extend project.document with Nextcloud integration fields."""

    _inherit = "project.document"

    # Nextcloud integration fields
    nc_config_id = fields.Many2one(
        "nextcloud.document.config",
        string="Config Nextcloud",
        help="Configuration Nextcloud utilisee pour ce document",
    )
    nc_file_path = fields.Char(
        string="Chemin Nextcloud",
        tracking=True,
        help="Chemin du fichier sur Nextcloud (ex: /Blue Fox/Documents/POL-SEC-FR.pdf)",
    )
    nc_file_id = fields.Integer(
        string="File ID Nextcloud",
        readonly=True,
        help="Identifiant interne du fichier sur Nextcloud",
    )
    nc_share_url = fields.Char(
        string="Lien de partage",
        readonly=True,
        help="URL de partage public generee via OCS",
    )
    nc_share_password = fields.Char(
        string="Mot de passe du partage",
        readonly=True,
        groups="base.group_system",
        help="Mot de passe du lien de partage (communiquer separement)",
    )
    nc_share_expiry = fields.Date(
        string="Expiration du partage",
        readonly=True,
    )
    nc_last_modified = fields.Datetime(
        string="Derniere modification NC",
        readonly=True,
        help="Date de derniere modification du fichier sur Nextcloud",
    )
    nc_file_size = fields.Integer(
        string="Taille du fichier (octets)",
        readonly=True,
    )
    nc_file_size_display = fields.Char(
        string="Taille",
        compute="_compute_nc_file_size_display",
    )
    nc_etag = fields.Char(
        string="ETag Nextcloud",
        readonly=True,
        copy=False,
    )
    nc_content_type = fields.Char(
        string="Type MIME",
        readonly=True,
    )
    nc_synced = fields.Boolean(
        string="Synchronise avec NC",
        compute="_compute_nc_synced",
        store=True,
    )

    # === Constraints ===

    @api.constrains("nc_file_path")
    def _check_nc_file_path(self):
        for record in self:
            if not record.nc_file_path:
                continue
            # Sanitize validates traversal, null bytes, etc.
            _sanitize_nc_path(record.nc_file_path)
            # Validate under project folder if set
            if record.project_id and record.project_id.nc_documents_folder:
                _validate_path_under_prefix(
                    record.nc_file_path,
                    record.project_id.nc_documents_folder,
                )
            # Reject angle brackets in filenames (HTML/XML injection risk)
            filename = posixpath.basename(record.nc_file_path)
            dangerous = set('<>')
            found = dangerous.intersection(filename)
            if found:
                raise ValidationError(
                    _(
                        "Le nom de fichier contient des caracteres interdits "
                        "(%s): %s"
                    )
                    % ("".join(found), filename)
                )

    @api.constrains("nc_share_url")
    def _check_nc_share_url(self):
        for record in self:
            if not record.nc_share_url:
                continue
            url = record.nc_share_url.lower()
            if not url.startswith("https://"):
                raise ValidationError(
                    _("L'URL de partage doit utiliser HTTPS.")
                )

    # === Computed Fields ===

    @api.depends("nc_file_size")
    def _compute_nc_file_size_display(self):
        for record in self:
            size = record.nc_file_size
            if not size:
                record.nc_file_size_display = ""
            elif size < 1024:
                record.nc_file_size_display = f"{size} o"
            elif size < 1024 * 1024:
                record.nc_file_size_display = f"{size / 1024:.1f} Ko"
            elif size < 1024 * 1024 * 1024:
                record.nc_file_size_display = f"{size / (1024*1024):.1f} Mo"
            else:
                record.nc_file_size_display = f"{size / (1024*1024*1024):.1f} Go"

    @api.depends("nc_file_path", "nc_config_id")
    def _compute_nc_synced(self):
        for record in self:
            record.nc_synced = bool(record.nc_file_path and record.nc_config_id)

    # === Onchange ===

    @api.onchange("project_id")
    def _onchange_project_nc_defaults(self):
        """Pre-fill NC config and path prefix from project settings."""
        if self.project_id:
            if self.project_id.nc_documents_config_id and not self.nc_config_id:
                self.nc_config_id = self.project_id.nc_documents_config_id
            if self.project_id.nc_documents_folder and not self.nc_file_path:
                self.nc_file_path = self.project_id.nc_documents_folder

    # === Actions ===

    def action_copy_nc_internal_link(self):
        """Return the Nextcloud internal link for copying to clipboard."""
        self.ensure_one()
        if self.nc_file_id and self.nc_config_id:
            base = self.nc_config_id.nextcloud_base_url.rstrip("/")
            url = f"{base}/f/{self.nc_file_id}"
        elif self.nc_file_path and self.nc_config_id:
            base = self.nc_config_id.nextcloud_base_url.rstrip("/")
            url = f"{base}/apps/files/?dir={posixpath.dirname(self.nc_file_path)}"
        elif self.external_url:
            url = self.external_url
        else:
            raise UserError(
                _("Aucun chemin Nextcloud ni URL externe configure pour ce document.")
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Lien interne Nextcloud"),
                "message": url,
                "type": "info",
                "sticky": True,
            },
        }

    def action_refresh_from_nextcloud(self):
        """Refresh file metadata from Nextcloud via PROPFIND."""
        self.ensure_one()
        if not self.nc_config_id or not self.nc_file_path:
            raise UserError(
                _(
                    "Configuration Nextcloud et chemin de fichier requis "
                    "pour rafraichir les metadonnees."
                )
            )

        config = self.nc_config_id
        path = _sanitize_nc_path(self.nc_file_path)

        try:
            results = config._webdav_propfind(path, depth="0")
        except UserError:
            raise
        except Exception as e:
            _logger.error("PROPFIND failed for %s: %s", path, e)
            raise UserError(
                _("Erreur lors de la consultation Nextcloud: %s") % str(e)
            )

        if not results:
            raise UserError(
                _("Fichier introuvable sur Nextcloud: %s") % path
            )

        entry = results[0]
        vals = {}

        if entry.get("last_modified"):
            try:
                dt = parsedate_to_datetime(entry["last_modified"])
                vals["nc_last_modified"] = fields.Datetime.to_string(dt)
            except Exception:
                pass

        if entry.get("size"):
            vals["nc_file_size"] = entry["size"]

        if entry.get("etag"):
            vals["nc_etag"] = entry["etag"]

        if entry.get("content_type"):
            vals["nc_content_type"] = entry["content_type"]

        if entry.get("file_id"):
            vals["nc_file_id"] = entry["file_id"]

        if vals:
            self.write(vals)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Rafraichissement"),
                "message": _("Metadonnees mises a jour depuis Nextcloud."),
                "type": "success",
                "sticky": False,
            },
        }

    def action_create_share_link(self):
        """Create a public share link for this document on Nextcloud.

        The share password is communicated via:
        1. Odoo sticky notification (visible only to the current user)
        2. Privatebin paste (burn-after-reading) as fallback
        The password is NEVER posted to chatter.
        """
        self.ensure_one()
        if not self.nc_config_id or not self.nc_file_path:
            raise UserError(
                _(
                    "Configuration Nextcloud et chemin de fichier requis "
                    "pour creer un lien de partage."
                )
            )

        config = self.nc_config_id
        share_info = config._ocs_create_share(self.nc_file_path)

        vals = {}
        if share_info.get("url"):
            vals["nc_share_url"] = share_info["url"]
            if not self.external_url:
                vals["external_url"] = share_info["url"]
        if share_info.get("password"):
            vals["nc_share_password"] = share_info["password"]
        if share_info.get("expire_date"):
            vals["nc_share_expiry"] = share_info["expire_date"]

        if vals:
            self.write(vals)

        # Post share URL to chatter (NO password)
        share_url_escaped = html_escape(share_info.get("url", ""))
        expire_escaped = html_escape(share_info.get("expire_date", "N/A"))
        self.message_post(
            body=(
                "<p><strong>Lien de partage Nextcloud cree</strong></p>"
                '<p>URL: <a href="%s">%s</a></p>'
                "<p>Expiration: %s</p>"
            ) % (share_url_escaped, share_url_escaped, expire_escaped),
            body_is_html=True,
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

        # Build notification with password for current user only
        notif_parts = [_("Lien de partage cree avec succes.")]
        if share_info.get("expire_date"):
            notif_parts.append(
                _("Expiration: %s") % share_info["expire_date"]
            )
        if share_info.get("password"):
            notif_parts.append(
                _("Mot de passe: %s") % share_info["password"]
            )

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Partage cree"),
                "message": "\n".join(notif_parts),
                "type": "success",
                "sticky": True,
            },
        }

    def action_upload_new_version(self):
        """Open a wizard to upload a new file version to Nextcloud."""
        self.ensure_one()
        if not self.nc_config_id:
            raise UserError(
                _("Configuration Nextcloud requise pour televerse une version.")
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Televerser une nouvelle version"),
            "res_model": "document.nc.upload.wizard",
            "views": [[False, "form"]],
            "target": "new",
            "context": {
                "default_document_id": self.id,
                "default_nc_config_id": self.nc_config_id.id,
                "default_nc_file_path": self.nc_file_path or "",
            },
        }

    def action_browse_nc_folder(self):
        """Open the file's parent folder in Nextcloud."""
        self.ensure_one()
        if self.nc_file_path and self.nc_config_id:
            base = self.nc_config_id.nextcloud_base_url.rstrip("/")
            folder = posixpath.dirname(self.nc_file_path)
            url = f"{base}/apps/files/?dir={folder}"
        elif self.project_id and self.project_id.nc_documents_folder and self.nc_config_id:
            base = self.nc_config_id.nextcloud_base_url.rstrip("/")
            folder = self.project_id.nc_documents_folder
            url = f"{base}/apps/files/?dir={folder}"
        else:
            raise UserError(
                _("Dossier Nextcloud et configuration requis pour naviguer.")
            )
        return {
            "type": "ir.actions.act_url",
            "url": url,
            "target": "new",
        }

    # === Version tracking for NC changes ===

    def _create_nc_change_version(
        self, doc, old_etag, new_etag,
        old_size, new_size, old_modified, new_modified,
    ):
        """Create a project.document.version to flag a detected NC change."""
        # Determine next version number
        existing = self.env["project.document.version"].search(
            [("document_id", "=", doc.id)],
            order="version_number desc",
            limit=1,
        )
        if existing:
            prev_num = existing.version_number or "0"
            # Try to increment the last numeric part
            parts = prev_num.rsplit(".", 1)
            try:
                parts[-1] = str(int(parts[-1]) + 1)
                next_num = ".".join(parts)
            except ValueError:
                next_num = f"{prev_num}.1"
        else:
            next_num = "1.0"

        # Build changelog with change details
        changelog_lines = [
            "Modification detectee automatiquement sur Nextcloud.",
            "",
            f"- ETag: `{old_etag}` -> `{new_etag}`",
        ]
        if old_size and new_size and old_size != new_size:
            delta = new_size - old_size
            sign = "+" if delta > 0 else ""
            changelog_lines.append(
                f"- Taille: {old_size} -> {new_size} octets ({sign}{delta})"
            )
        if new_modified:
            changelog_lines.append(
                f"- Modifie le: {new_modified.strftime('%Y-%m-%d %H:%M:%S')}"
            )

        version_vals = {
            "document_id": doc.id,
            "version_number": next_num,
            "change_type": "editorial",
            "change_summary": _(
                "Modification detectee sur Nextcloud"
            ),
            "changelog": "\n".join(changelog_lines),
            "state": "draft",
            "effective_date": fields.Date.today(),
        }
        if existing:
            version_vals["previous_version_id"] = existing.id

        version = self.env["project.document.version"].create(version_vals)
        _logger.info(
            "Created version %s (id=%d) for doc %s (NC change detected)",
            next_num, version.id, doc.code,
        )
        return version

    # === Cron: Detect modified files ===

    @api.model
    def _cron_check_nc_modifications(self):
        """Check all NC-linked documents for modifications on Nextcloud.

        Creates activities when files have been modified since last check.
        """
        documents = self.search([
            ("nc_config_id", "!=", False),
            ("nc_file_path", "!=", False),
            ("state", "!=", "archived"),
        ])

        configs = {}
        for doc in documents:
            config_id = doc.nc_config_id.id
            if config_id not in configs:
                configs[config_id] = []
            configs[config_id].append(doc)

        for config_id, docs in configs.items():
            config = self.env["nextcloud.document.config"].browse(config_id)
            if not config.exists() or not config.active:
                continue

            updated_count = 0
            error_count = 0

            for doc in docs:
                try:
                    results = config._webdav_propfind(
                        doc.nc_file_path, depth="0"
                    )
                    if not results:
                        continue

                    entry = results[0]
                    new_etag = entry.get("etag")

                    # Compare ETag to detect changes
                    if new_etag and doc.nc_etag and new_etag != doc.nc_etag:
                        old_etag = doc.nc_etag
                        old_size = doc.nc_file_size
                        old_modified = doc.nc_last_modified

                        vals = {"nc_etag": new_etag}
                        new_size = entry.get("size")
                        new_modified = None
                        if new_size:
                            vals["nc_file_size"] = new_size
                        if entry.get("last_modified"):
                            try:
                                dt = parsedate_to_datetime(entry["last_modified"])
                                new_modified = dt
                                vals["nc_last_modified"] = fields.Datetime.to_string(dt)
                            except Exception:
                                pass

                        doc.write(vals)

                        # Create a version record to track the change
                        self._create_nc_change_version(
                            doc, old_etag, new_etag,
                            old_size, new_size,
                            old_modified, new_modified,
                        )

                        # Create activity for document owner
                        owner = doc.owner_id or doc.author_id
                        if owner:
                            doc.activity_schedule(
                                "mail.mail_activity_data_todo",
                                user_id=owner.id,
                                summary=_(
                                    "Fichier modifie sur Nextcloud: %s"
                                ) % doc.name,
                                note=_(
                                    "Le fichier '%s' a ete modifie sur Nextcloud "
                                    "depuis la derniere verification."
                                ) % posixpath.basename(doc.nc_file_path),
                            )
                        updated_count += 1

                    elif not doc.nc_etag and new_etag:
                        # First time: just store the etag
                        vals = {"nc_etag": new_etag}
                        if entry.get("size"):
                            vals["nc_file_size"] = entry["size"]
                        if entry.get("file_id"):
                            vals["nc_file_id"] = entry["file_id"]
                        if entry.get("last_modified"):
                            try:
                                dt = parsedate_to_datetime(entry["last_modified"])
                                vals["nc_last_modified"] = fields.Datetime.to_string(dt)
                            except Exception:
                                pass
                        doc.write(vals)

                except Exception as e:
                    error_count += 1
                    _logger.warning(
                        "NC modification check failed for doc %s: %s",
                        doc.id, e,
                    )

            # Update config status
            if error_count:
                status = "partial" if updated_count else "error"
                msg = _(
                    "Verification: %d modifie(s), %d erreur(s) sur %d documents"
                ) % (updated_count, error_count, len(docs))
            else:
                status = "success"
                msg = _(
                    "Verification: %d modifie(s) sur %d documents"
                ) % (updated_count, len(docs))

            config.write({
                "last_sync": fields.Datetime.now(),
                "last_sync_status": status,
                "last_sync_message": msg,
            })

        _logger.info(
            "NC modification check completed for %d configs, %d documents",
            len(configs), len(documents),
        )
