from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SecureTransferRevealWizard(models.TransientModel):
    """Two-step reveal of a transfer's share link (bf_sign pattern).

    The token is manager-only at rest; this wizard is the sanctioned way to
    read it from the backend. Opening the wizard only shows a warning; the
    link is revealed **and** the link_revealed entry is appended to the
    tamper-evident trail **only** when the manager confirms — so closing or
    cancelling reveals and logs nothing.
    """

    _name = "secure.transfer.reveal.wizard"
    _description = "Révéler le lien de partage (journalisé)"

    transfer_id = fields.Many2one(
        "secure.transfer", string="Transfert", readonly=True,
    )
    url = fields.Char(string="Lien de partage", readonly=True)
    revealed = fields.Boolean(default=False)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        transfer_id = (
            self.env.context.get("default_transfer_id")
            or self.env.context.get("active_id")
        )
        transfer = self.env["secure.transfer"].browse(transfer_id or []).exists()
        if not transfer:
            raise UserError(_("Aucun transfert sélectionné."))
        if not self.env.user.has_group(
            "bf_securetransfer.group_securetransfer_manager"
        ):
            raise UserError(_(
                "Action réservée aux gestionnaires du transfert sécurisé."
            ))
        res["transfer_id"] = transfer.id
        return res

    def action_confirm_reveal(self):
        """Log the reveal in the access trail, then expose the link."""
        self.ensure_one()
        if not self.env.user.has_group(
            "bf_securetransfer.group_securetransfer_manager"
        ):
            raise UserError(_(
                "Action réservée aux gestionnaires du transfert sécurisé."
            ))
        transfer = self.transfer_id.sudo()
        transfer._log(
            "link_revealed",
            actor=self.env.user.login,
            note=_("Lien de partage révélé depuis le backend."),
        )
        self.write({"url": transfer._share_url(), "revealed": True})
        return {
            "type": "ir.actions.act_window",
            "name": _("Lien de partage"),
            "res_model": "secure.transfer.reveal.wizard",
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
        }
