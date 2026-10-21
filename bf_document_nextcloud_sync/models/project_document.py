import logging
import posixpath
from email.utils import parsedate_to_datetime
from markupsafe import Markup

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
        string="Nextcloud config",
        help="Nextcloud configuration used for this document",
    )
    nc_file_path = fields.Char(
        string="Nextcloud path",
        tracking=True,
        help="Path of the file on Nextcloud (e.g. /Blue "
             "Fox/Documents/POL-SEC-FR.pdf)",
    )
    nc_file_id = fields.Integer(
        string="Nextcloud file ID",
        readonly=True,
        help="Internal ID of the file on Nextcloud",
    )
    nc_share_url = fields.Char(
        string="Share link",
        readonly=True,
        help="Public share URL generated through OCS",
    )
    nc_share_password = fields.Char(
        string="Share password",
        readonly=True,
        groups="base.group_system",
        help="Share link password (send it separately)",
    )
    nc_share_expiry = fields.Date(
        string="Share expiry",
        readonly=True,
    )
    nc_last_modified = fields.Datetime(
        string="NC last modified",
        readonly=True,
        help="Last modification date of the file on Nextcloud",
    )
    nc_file_size = fields.Integer(
        string="File size (bytes)",
        readonly=True,
    )
    nc_file_size_display = fields.Char(
        string="Size",
        compute="_compute_nc_file_size_display",
    )
    nc_etag = fields.Char(
        string="Nextcloud ETag",
        readonly=True,
        copy=False,
    )
    nc_content_type = fields.Char(
        string="MIME type",
        readonly=True,
    )
    nc_synced = fields.Boolean(
        string="Synced with NC",
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
                        "The file name contains forbidden characters (%s): %s"
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
                    _("The share URL must use HTTPS.")
                )

    # === Computed Fields ===

    @api.depends("nc_file_size")
    @api.depends_context("lang")
    def _compute_nc_file_size_display(self):
        # Non stocké : les unités suivent la langue de qui lit (« Ko » en français, « KB » en anglais).
        for record in self:
            size = record.nc_file_size
            env = record.env
            if not size:
                record.nc_file_size_display = ""
            elif size < 1024:
                record.nc_file_size_display = env._("%s B", size)
            elif size < 1024 * 1024:
                record.nc_file_size_display = env._("%s KB", f"{size / 1024:.1f}")
            elif size < 1024 * 1024 * 1024:
                record.nc_file_size_display = env._("%s MB", f"{size / (1024 * 1024):.1f}")
            else:
                record.nc_file_size_display = env._("%s GB", f"{size / (1024 * 1024 * 1024):.1f}")

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
                _("No Nextcloud path or external URL is set for this document.")
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Nextcloud internal link"),
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
                    "A Nextcloud configuration and a file path are "
                    "required to refresh the metadata."
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
                _("Error while querying Nextcloud: %s") % str(e)
            )

        if not results:
            raise UserError(
                _("File not found on Nextcloud: %s") % path
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
                "title": _("Refresh"),
                "message": _("Metadata updated from Nextcloud."),
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
                    "A Nextcloud configuration and a file path are "
                    "required to create a share link."
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

        # Post share URL to chatter (NO password), in the language of the
        # person who made the share; the values are escaped by Markup's `%`.
        share_url = share_info.get("url", "")
        self.message_post(
            body=Markup(self.env._(
                "<p><strong>Nextcloud share link created</strong></p>"
                "<p>URL: <a href=\"%(url)s\">%(url)s</a></p>"
                "<p>Expiry: %(expiry)s</p>"
            )) % {
                "url": share_url,
                "expiry": share_info.get("expire_date") or self.env._("N/A"),
            },
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

        # Build notification with password for current user only
        notif_parts = [_("Share link created.")]
        if share_info.get("expire_date"):
            notif_parts.append(
                _("Expiry: %s") % share_info["expire_date"]
            )
        if share_info.get("password"):
            notif_parts.append(
                _("Password: %s") % share_info["password"]
            )

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Share created"),
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
                _("A Nextcloud configuration is required to upload a version.")
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Upload a new version"),
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
                _("A Nextcloud folder and a configuration are required to "
                  "browse.")
            )
        return {
            "type": "ir.actions.act_url",
            "url": url,
            "target": "new",
        }

    # === Version tracking for NC changes ===

    @api.model
    def _nc_installed_lang(self, *candidates):
        """The first of `candidates` that is installed, else an installed one.

        A partner may keep the code of a language deactivated since: pinned
        in the context, it would fail the job after the ETag is written (the
        version and the activity lost)."""
        installed = [code for code, _name in self.env["res.lang"].get_installed()]
        for lang in candidates:
            if lang and lang in installed:
                return lang
        return "en_US" if "en_US" in installed or not installed else installed[0]

    @api.model
    def _nc_reader_lang(self, doc):
        """Who reads what the change check writes: the document's owner (or
        author), else its company. The check runs as a scheduled job, with no
        person's language in its context."""
        owner = doc.owner_id or doc.author_id
        company = doc.company_id or self.env.company
        return self._nc_installed_lang(owner.lang, company.partner_id.lang, self.env.lang)

    def _create_nc_change_version(
        self, doc, old_etag, new_etag,
        old_size, new_size, old_modified, new_modified,
    ):
        """Create a project.document.version to flag a detected NC change.

        Its summary and changelog are stored text: written in the language of
        the document's owner (see _nc_reader_lang), not in the job's.
        """
        env = doc.with_context(lang=self._nc_reader_lang(doc)).env
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
            env._("Change detected automatically on Nextcloud."),
            "",
            f"- ETag: `{old_etag}` -> `{new_etag}`",
        ]
        if old_size and new_size and old_size != new_size:
            delta = new_size - old_size
            sign = "+" if delta > 0 else ""
            changelog_lines.append(
                env._("- Size: %(old)s -> %(new)s bytes (%(delta)s)",
                      old=old_size, new=new_size, delta=f"{sign}{delta}")
            )
        if new_modified:
            changelog_lines.append(
                env._("- Modified on: %s", new_modified.strftime('%Y-%m-%d %H:%M:%S'))
            )

        version_vals = {
            "document_id": doc.id,
            "version_number": next_num,
            "change_type": "editorial",
            "change_summary": env._("Change detected on Nextcloud"),
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
                        # Created in the assignee's language: summary, note
                        # and the subject of Odoo's notice alike.
                        owner = doc.owner_id or doc.author_id
                        if owner:
                            doc_owner = doc.with_context(lang=self._nc_reader_lang(doc))
                            doc_owner.activity_schedule(
                                "mail.mail_activity_data_todo",
                                user_id=owner.id,
                                summary=doc_owner.env._(
                                    "File changed on Nextcloud: %s"
                                ) % doc.name,
                                note=doc_owner.env._(
                                    "The file '%s' was changed on "
                                    "Nextcloud since the last check."
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

            # Update config status: read by the operators, in the language
            # of the configuration's company (the job has none of its own).
            env = config.with_context(
                lang=self._nc_installed_lang(config.company_id.partner_id.lang, self.env.lang)
            ).env
            if error_count:
                status = "partial" if updated_count else "error"
                msg = env._(
                    "Check: %d changed, %d error(s) out of %d documents"
                ) % (updated_count, error_count, len(docs))
            else:
                status = "success"
                msg = env._(
                    "Check: %d changed out of %d documents"
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
