"""A project's Nextcloud folder: a subfolder of its client's folder (18.0.4.2.0).

The folder typed by hand on the project (`nc_documents_folder`, from
bf_document_nextcloud_sync) still wins when it is set. It is the only way a
project WITHOUT a client gets a folder.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .bf_nc_client_folder import clean_subfolder, joined_folder


class ProjectProject(models.Model):
    _inherit = "project.project"

    nc_client_partner_id = fields.Many2one(
        "res.partner",
        string="Client (folder)",
        related="partner_id.commercial_partner_id",
        groups="base.group_user,bf_nextcloud_browser.group_nc_browser_user",
    )
    nc_client_folder_id = fields.Many2one(
        "bf.nc.client.folder",
        string="Client folder",
        compute="_compute_nc_client_folder_id",
        store=True,
        readonly=False,
        ondelete="set null",
        groups="base.group_user,bf_nextcloud_browser.group_nc_browser_user",
        domain="[('partner_id', '=', nc_client_partner_id)]",
        help="The client folder this project lives in. Picked "
             "automatically when the client has only one.",
    )
    nc_project_subfolder = fields.Char(
        string="Project subfolder",
        copy=False,
        groups="base.group_user,bf_nextcloud_browser.group_nc_browser_user",
        help="Path relative to the client folder, e.g. (PROJETS)/001 - "
             "Rollout. Empty: the project opens the client's folder.",
    )
    nc_browser_folder = fields.Char(
        string="Nextcloud folder (browser)",
        compute="_compute_nc_browser_folder",
        compute_sudo=True,
        groups="base.group_user,bf_nextcloud_browser.group_nc_browser_user",
    )
    nc_client_folder_available = fields.Boolean(
        compute="_compute_nc_browser_folder",
        compute_sudo=True,
        groups="base.group_user,bf_nextcloud_browser.group_nc_browser_user",
    )

    @api.depends("partner_id.commercial_partner_id.nc_client_folder_ids")
    def _compute_nc_client_folder_id(self):
        for project in self:
            folders = project.partner_id.commercial_partner_id.nc_client_folder_ids
            # A choice already made among the client's folders is kept.
            if project.nc_client_folder_id and project.nc_client_folder_id in folders:
                continue
            project.nc_client_folder_id = folders[:1]

    @api.depends(
        "nc_documents_folder",
        "nc_client_folder_id.path",
        "nc_project_subfolder",
        "partner_id.commercial_partner_id.nc_client_folder_ids",
    )
    def _compute_nc_browser_folder(self):
        for project in self:
            project.nc_client_folder_available = bool(
                project.partner_id.commercial_partner_id.nc_client_folder_ids
            )
            if project.nc_documents_folder:
                project.nc_browser_folder = project.nc_documents_folder
            elif project.nc_client_folder_id:
                project.nc_browser_folder = joined_folder(
                    project.nc_client_folder_id.path, project.nc_project_subfolder
                )
            else:
                project.nc_browser_folder = False

    @api.constrains("nc_project_subfolder")
    def _check_nc_project_subfolder(self):
        for project in self:
            clean_subfolder(project.nc_project_subfolder)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("nc_project_subfolder"):
                vals["nc_project_subfolder"] = clean_subfolder(vals["nc_project_subfolder"]) or False
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("nc_project_subfolder"):
            vals["nc_project_subfolder"] = clean_subfolder(vals["nc_project_subfolder"]) or False
        # A subfolder belongs to the folder it was chosen in. A change of client
        # or of folder outside the form (RPC, import, merge) must not carry it
        # into another client's folder; the onchange only covers the form.
        watch = ("partner_id" in vals or "nc_client_folder_id" in vals) and "nc_project_subfolder" not in vals
        before = {p.id: p.nc_client_folder_id for p in self.sudo()} if watch else {}
        res = super().write(vals)
        if watch:
            moved = self.filtered(
                lambda p: p.sudo().nc_project_subfolder and p.sudo().nc_client_folder_id != before[p.id]
            )
            if moved:
                # Through the whole chain (audit log included), as the same person.
                moved.write({"nc_project_subfolder": False})
        return res

    @api.onchange("nc_client_folder_id")
    def _onchange_nc_client_folder_id(self):
        # A subfolder of another client's folder would open nothing. On a new
        # project there is no previous folder: what was typed stays.
        if self._origin.nc_client_folder_id and self._origin.nc_client_folder_id != self.nc_client_folder_id:
            self.nc_project_subfolder = False

    def action_nc_pick_folder(self):
        """Offer the client's folders and their project subfolders, closest first."""
        self.ensure_one()
        if not self.partner_id.commercial_partner_id.nc_client_folder_ids:
            raise UserError(
                _("First link a Nextcloud folder to the client %s.")
                % (self.partner_id.commercial_partner_id.display_name or _("of the project"))
            )
        return self.env["bf.nc.folder.pick"]._open_for(self)
