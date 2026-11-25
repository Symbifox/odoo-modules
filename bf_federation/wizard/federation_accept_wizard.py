from odoo import _, fields, models
from odoo.exceptions import UserError


class FederationAcceptWizard(models.TransientModel):
    _name = "federation.accept.wizard"
    _description = "Accept a federation invitation"

    base_url = fields.Char(string="Peer address", required=True, help="https://… "
                                                                         "of "
                                                                         "the "
                                                                         "instance "
                                                                         "that "
                                                                         "is "
                                                                         "inviting "
                                                                         "you")
    code = fields.Char(string="Invitation code", required=True)
    name = fields.Char(string="Peer name", help="Leave empty to use the "
                                                  "name the peer "
                                                  "announces.")
    mirror_user_id = fields.Many2one("res.users", string="Assign received "
                                                         "tasks to", required=True,
                                     default=lambda self: self.env.user,
                                     domain="[('share', '=', False), ('active', '=', True)]")
    send_notes = fields.Boolean(string="Also send my internal notes to "
                                       "this peer")

    def action_accept(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise UserError(_("Pairing is reserved for administrators."))
        peer = self.env["federation.peer"].create({
            "name": self.name or self.base_url, "base_url": self.base_url.strip().rstrip("/"),
            "mirror_user_id": self.mirror_user_id.id, "send_notes": self.send_notes,
        })
        peer.action_accept_invitation(self.code.strip())
        return {"type": "ir.actions.act_window", "res_model": "federation.peer", "res_id": peer.id,
                "view_mode": "form", "views": [(False, "form")], "target": "current"}
