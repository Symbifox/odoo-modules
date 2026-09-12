from odoo import _, fields, models
from odoo.exceptions import UserError


class FederationAcceptWizard(models.TransientModel):
    _name = "federation.accept.wizard"
    _description = "Accepter une invitation de fédération"

    base_url = fields.Char(string="Adresse du pair", required=True, help="https://… de l'instance qui vous a invité")
    code = fields.Char(string="Code d'invitation", required=True)
    name = fields.Char(string="Nom du pair", help="Laissez vide pour prendre le nom que le pair annonce.")
    mirror_user_id = fields.Many2one("res.users", string="Assigner les tâches reçues à", required=True,
                                     default=lambda self: self.env.user)
    send_notes = fields.Boolean(string="Envoyer aussi mes notes internes à ce pair")

    def action_accept(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise UserError(_("Le jumelage est réservé aux administrateurs."))
        peer = self.env["federation.peer"].create({
            "name": self.name or self.base_url, "base_url": self.base_url.strip().rstrip("/"),
            "mirror_user_id": self.mirror_user_id.id, "send_notes": self.send_notes,
        })
        peer.action_accept_invitation(self.code.strip())
        return {"type": "ir.actions.act_window", "res_model": "federation.peer", "res_id": peer.id,
                "view_mode": "form", "views": [(False, "form")], "target": "current"}
