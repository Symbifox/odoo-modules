"""Choose the Nextcloud folder of a client or of a project (18.0.4.2.0).

The folders are listed in Nextcloud AS THE PERSON (their own connection, as
the browser does), closest names first. Nothing is guessed silently: the
person picks one line.

  * a client (company): the folders directly under the configured clients root;
  * a project: each folder of its client, and the subfolders of that folder's
    projects subfolder, « (PROJETS) » by default.
"""

import posixpath
import unicodedata

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import nc_match
from .bf_nc_client_folder import clean_folder_path
from .nc_identity import NcPathNotFound

PICK_MODELS = ("res.partner", "project.project")


def _needed_access(record):
    """Linking a client's folder creates a folder record and leaves the company
    untouched: reading it is enough (a member without « Création de contact »
    was refused, seen on the bench in the role). A project's folder is written
    ON the project."""
    return "write" if record._name == "project.project" else "read"


class BfNcFolderPick(models.TransientModel):
    _name = "bf.nc.folder.pick"
    _description = "Choose a Nextcloud folder"

    res_model = fields.Char(required=True, readonly=True)
    res_id = fields.Integer(required=True, readonly=True)
    target_name = fields.Char(string="For", readonly=True)
    line_ids = fields.One2many("bf.nc.folder.pick.line", "pick_id", string="Folders")
    note = fields.Char(readonly=True)

    # ------------------------------------------------------------------
    @api.model
    def _open_for(self, record):
        browser = self.env["bf.nc.browser"]
        browser._check_access()
        if record._name not in PICK_MODELS:
            raise UserError(_("Model not supported: %s") % record._name)
        record.check_access(_needed_access(record))
        config = browser._default_config()
        if not config or not config._browser_prefix_ok():
            raise UserError(_("No usable Nextcloud configuration (browser "
                              "root prefix)."))
        person = browser._as_person(config)
        if record._name == "res.partner":
            lines, note = self._client_lines(browser, person, config, record)
        else:
            lines, note = self._project_lines(browser, person, config, record)
        lines.sort(key=lambda l: (-l["score"], l["name"].lower()))
        pick = self.create({
            "res_model": record._name,
            "res_id": record.id,
            "target_name": record.display_name,
            "note": note,
            "line_ids": [(0, 0, l) for l in lines],
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Choose the Nextcloud folder"),
            "res_model": self._name,
            "res_id": pick.id,
            "view_mode": "form",
            "target": "new",
        }

    def _subfolders(self, browser, person, path):
        """The folders directly under `path`, as (name, absolute path)."""
        raw = person._webdav_propfind(path, depth="1")
        entries = browser._entries(person, raw, path, skip=(path,))
        return [
            (e["name"], posixpath.join(path, e["rel"]))
            for e in entries
            if e["is_dir"] and not e["is_hidden"]
        ]

    def _client_lines(self, browser, person, config, partner):
        root = config._nc_clients_root_path()
        if not root:
            raise UserError(_(
                "Set \"Client folders root\" on the Nextcloud "
                "configuration (e.g. /Company/(Clients)/)."
            ))
        # The client and its contacts may hold the folder's whole name; a
        # project only helps (« 009 - Chantier Sérénité » for 1234-5678 Québec).
        names = [partner.name] + partner.child_ids.mapped("name")
        projects = self.env["project.project"].sudo().search(
            [("partner_id", "child_of", partner.id)]
        ).mapped("name")
        taken = {
            unicodedata.normalize("NFC", f.path): f.partner_id
            for f in self.env["bf.nc.client.folder"].sudo().search([])
        }
        lines = []
        for name, path in self._subfolders(browser, person, root):
            owner = taken.get(unicodedata.normalize("NFC", path))
            if owner == partner:
                continue
            lines.append({
                "name": name,
                "path": path,
                "score": 0 if owner else round(100 * max(
                    nc_match.best(name, names, loose=True),
                    0.9 * nc_match.best(name, projects),
                )),
                "taken_by": owner.display_name if owner else False,
            })
        note = _("%d folders under %s.") % (len(lines), root)
        return lines, note

    def _project_lines(self, browser, person, config, project):
        sub_name = (config.nc_projects_subfolder or "").strip()
        lines = []
        for folder in project.partner_id.commercial_partner_id.nc_client_folder_ids:
            lines.append({
                "name": _("%s (client folder)") % folder.name,
                "path": folder.path,
                "folder_id": folder.id,
                "subfolder": False,
                "score": 0,
            })
            parent = posixpath.join(folder.path, sub_name) if sub_name else folder.path
            try:
                found = self._subfolders(browser, person, parent)
            except NcPathNotFound:
                found = []  # no projects subfolder in this client's folder
            for name, path in found:
                lines.append({
                    "name": name,
                    "path": path,
                    "folder_id": folder.id,
                    "subfolder": posixpath.relpath(path, folder.path),
                    "score": round(100 * nc_match.similarity(name, project.name)),
                })
        return lines, False


class BfNcFolderPickLine(models.TransientModel):
    _name = "bf.nc.folder.pick.line"
    _description = "Suggested Nextcloud folder"
    _order = "score desc, name"

    pick_id = fields.Many2one("bf.nc.folder.pick", required=True, ondelete="cascade")
    name = fields.Char(string="Folder", readonly=True)
    path = fields.Char(string="Path", readonly=True)
    score = fields.Integer(string="Similarity (%)", readonly=True)
    taken_by = fields.Char(string="Already linked to", readonly=True)
    folder_id = fields.Many2one("bf.nc.client.folder", readonly=True)
    subfolder = fields.Char(readonly=True)

    def action_choose(self):
        self.ensure_one()
        pick = self.pick_id
        self.env["bf.nc.browser"]._check_access()
        if pick.res_model not in PICK_MODELS:
            raise UserError(_("Model not supported: %s") % pick.res_model)
        record = self.env[pick.res_model].browse(pick.res_id).exists()
        if not record:
            raise UserError(_("Record not found."))
        record.check_access(_needed_access(record))
        if pick.res_model == "res.partner":
            if self.taken_by:
                raise UserError(_("This folder is already linked to %s.") % self.taken_by)
            self.env["bf.nc.client.folder"].create({
                "partner_id": record.commercial_partner_id.id,
                "path": clean_folder_path(self.path),
                "sequence": 10 + 10 * len(record.commercial_partner_id.nc_client_folder_ids),
            })
        else:
            if self.folder_id not in record.partner_id.commercial_partner_id.nc_client_folder_ids:
                raise UserError(_("This folder does not belong to the "
                                  "project's client."))
            record.write({
                "nc_client_folder_id": self.folder_id.id,
                "nc_project_subfolder": self.subfolder or False,
            })
        # The listing was made with the person's Nextcloud account: it does not
        # outlive the choice.
        pick.unlink()
        return {"type": "ir.actions.act_window_close"}
