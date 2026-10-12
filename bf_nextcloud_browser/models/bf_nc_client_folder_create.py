"""Create a client's Nextcloud folder from the template (18.0.4.3.0)."""

from odoo import fields, models


class BfNcClientFolderCreate(models.TransientModel):
    _name = "bf.nc.client.folder.create"
    _description = "Créer le dossier Nextcloud d'un client"

    partner_id = fields.Many2one("res.partner", string="Client", required=True, readonly=True)
    name = fields.Char(string="Nom du dossier", required=True)
    root = fields.Char(string="Dans", readonly=True)
    template = fields.Char(string="Copie de", readonly=True)

    def action_create(self):
        self.ensure_one()
        self.partner_id.nc_create_client_folder(self.name)
        self.unlink()
        return {"type": "ir.actions.act_window_close"}
