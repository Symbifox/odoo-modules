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
    _description = "Reveal the share link (logged)"

    transfer_id = fields.Many2one(
        "secure.transfer", string="Transfer", readonly=True,
    )
    url = fields.Char(string="Share link", readonly=True)
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
            raise UserError(_("No transfer selected."))
        if not self.env.user.has_group(
            "bf_securetransfer.group_securetransfer_manager"
        ):
            raise UserError(_(
                "Action restricted to secure transfer managers."
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
                "Action restricted to secure transfer managers."
            ))
        transfer = self.transfer_id.sudo()
        transfer._log(
            "link_revealed",
            actor=self.env.user.login,
            note=transfer._log_env()._("Share link revealed from the backend."),
        )
        self.write({"url": transfer._share_url(), "revealed": True})
        return {
            "type": "ir.actions.act_window",
            "name": _("Share link"),
            "res_model": "secure.transfer.reveal.wizard",
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
        }
