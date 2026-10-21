"""Create a client's Nextcloud folder from the template (18.0.4.3.0)."""

from odoo import fields, models


class BfNcClientFolderCreate(models.TransientModel):
    _name = "bf.nc.client.folder.create"
    _description = "Create a client's Nextcloud folder"

    partner_id = fields.Many2one("res.partner", string="Client", required=True, readonly=True)
    name = fields.Char(string="Folder name", required=True)
    root = fields.Char(string="Inside", readonly=True)
    template = fields.Char(string="Copy of", readonly=True)

    def action_create(self):
        self.ensure_one()
        self.partner_id.nc_create_client_folder(self.name)
        self.unlink()
        return {"type": "ir.actions.act_window_close"}
