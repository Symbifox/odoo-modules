"""A client's Nextcloud folder, attached to the company (18.0.4.2.0).

A common layout: one folder per CLIENT under a clients root such as
/Company/(Clients)/, one subfolder of (PROJETS) per project, and before this
version no project carrying a folder. The record-level browser never
showed, because Odoo knew no folder; the onboarding created the folder and
wrote the link nowhere.

A folder belongs to the commercial entity (a company, or a person who is their
own client). A company may own several (one person's own folder and the folder
of their organisation). Projects pick one of them, and a subfolder.
"""

import posixpath
import unicodedata

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.bf_document_nextcloud_sync.models.nextcloud_document_config import (
    _sanitize_nc_path,
    _validate_path_under_prefix,
)


def clean_folder_path(path):
    """An absolute Nextcloud folder path, normalised; refuses '/' and traversal.

    Unicode goes to NFC, as Nextcloud stores names: « Sérénité » typed in one
    form and listed in the other is the same folder, and must be one record.
    """
    path = unicodedata.normalize("NFC", (path or "").strip())
    if not path:
        raise ValidationError(_("The folder path is required."))
    clean = posixpath.normpath(_sanitize_nc_path(path))
    if clean == "/":
        raise ValidationError(_("A client folder cannot be the root '/'."))
    return clean


def clean_subfolder(sub):
    """A path relative to a client folder, or "" ; refuses '..' and decoding names."""
    sub = unicodedata.normalize("NFC", (sub or "").strip().strip("/"))
    if not sub:
        return ""
    clean = posixpath.normpath(_sanitize_nc_path("/" + sub)).lstrip("/")
    return "" if clean in ("", ".") else clean


def joined_folder(base, sub):
    """`base` + `sub` for a compute: never raises (a form must still open).

    A subfolder the sanitiser refuses is joined as typed; the browser's
    _resolve_root sanitises the result again and refuses it there, with a
    message, before any request leaves.
    """
    if not base:
        return False
    try:
        sub = clean_subfolder(sub)
    except ValidationError:
        sub = (sub or "").strip().strip("/")
    return posixpath.join(base, sub) if sub else base


class BfNcClientFolder(models.Model):
    _name = "bf.nc.client.folder"
    _description = "Nextcloud client folder"
    _order = "partner_id, sequence, id"

    partner_id = fields.Many2one(
        "res.partner",
        string="Client",
        required=True,
        ondelete="cascade",
        index=True,
        domain="[('parent_id', '=', False)]",
        help="The company, or the person who is a client themselves.",
    )
    path = fields.Char(string="Nextcloud path", required=True)
    name = fields.Char(string="Folder", compute="_compute_name", store=True)
    sequence = fields.Integer(default=10)

    _sql_constraints = [
        ("path_uniq", "unique(path)", "This Nextcloud folder is already "
                                      "linked to a client."),
    ]

    @api.depends("path")
    def _compute_name(self):
        for folder in self:
            folder.name = posixpath.basename((folder.path or "").rstrip("/")) or folder.path

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if "path" in vals:
                vals["path"] = clean_folder_path(vals["path"])
            if vals.get("partner_id"):
                vals["partner_id"] = self.env["res.partner"].browse(
                    vals["partner_id"]
                ).commercial_partner_id.id
        return super().create(vals_list)

    def write(self, vals):
        if "path" in vals:
            vals["path"] = clean_folder_path(vals["path"])
        if vals.get("partner_id"):
            vals["partner_id"] = self.env["res.partner"].browse(
                vals["partner_id"]
            ).commercial_partner_id.id
        return super().write(vals)

    @api.constrains("path")
    def _check_path_under_prefix(self):
        config = self.env["bf.nc.browser"]._default_config()
        root = config._nc_clients_root_path() if config else ""
        for folder in self:
            if root and posixpath.normpath(folder.path) == root:
                raise ValidationError(_("A client folder cannot be the "
                                        "client folders root."))
        prefix = (config.browser_root_prefix or "").strip() if config else ""
        if prefix in ("", "/"):
            return
        prefix = _sanitize_nc_path(prefix)
        for folder in self:
            _validate_path_under_prefix(folder.path, prefix)
            if posixpath.normpath(folder.path) == posixpath.normpath(prefix):
                raise ValidationError(_("A client folder cannot be the "
                                        "browser's root."))

    @api.constrains("path")
    def _check_not_nested(self):
        """One client's folder inside another's would show the first client's
        files on the second client's records."""
        for folder in self:
            # sudo: a folder hidden from the person by a rule still counts.
            others = self.sudo().search([("id", "!=", folder.id)])
            for other in others:
                a, b = folder.path, other.path
                if a.startswith(b + "/") or b.startswith(a + "/"):
                    raise ValidationError(_(
                        "\"%(a)s\" and \"%(b)s\" are nested: a client "
                        "folder cannot contain another one.", a=a, b=b,
                    ))

    def unlink(self):
        # A project's subfolder belongs to the folder it was chosen in.
        self.env["project.project"].sudo().with_context(active_test=False).search(
            [("nc_client_folder_id", "in", self.ids)]
        ).write({"nc_project_subfolder": False})
        return super().unlink()
