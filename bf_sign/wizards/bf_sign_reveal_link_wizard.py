from odoo import _, fields, models
from odoo.exceptions import UserError


class BfSignRevealLinkWizard(models.TransientModel):
    """Two-step reveal of a signer's personal signing link.

    Opening the wizard only shows a warning; the link is revealed **and** the
    reveal is written to the tamper-evident audit trail **only** when the
    manager confirms — so closing/cancelling reveals and logs nothing.

    A transient record is not private to its creator in Odoo 18: without the
    record rule ``rule_sign_reveal_link_wizard_own`` every sign manager could
    read the links the others revealed, and that read would leave no trace in
    the audit trail. The link is also blanked when the manager closes the
    wizard, instead of waiting for the transient vacuum (18.0.3.28.1).
    """

    _name = "bf.sign.reveal.link.wizard"
    _description = "Révéler le lien de signature (journalisé)"

    signer_id = fields.Many2one("bf.sign.signer", readonly=True)
    url = fields.Char(string="Lien de signature", readonly=True)
    revealed = fields.Boolean(default=False)

    def action_confirm_reveal(self):
        """Log the reveal in the audit trail, then expose the link."""
        self.ensure_one()
        if not self.env.user.has_group("bf_sign.group_sign_manager"):
            raise UserError(_("Action réservée aux gestionnaires de signature."))
        # The wizard can be created by RPC with any signer_id: the signer must be
        # one this manager may read (record rules, companies) before sudo.
        self.signer_id.check_access("read")
        signer = self.signer_id.sudo()
        self.env["bf.sign.log"].sudo()._append(
            signer.request_id, "link_revealed", actor=self.env.user.name,
            identity_method="internal_user",
            note=_("Lien de signature révélé pour le signataire « %s ».") % (signer.name or ""))
        self.write({"url": signer._signing_url(), "revealed": True})
        return {
            "type": "ir.actions.act_window",
            "name": _("Lien de signature"),
            "res_model": "bf.sign.reveal.link.wizard",
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
        }

    def action_close(self):
        """Blank the revealed link, then close the dialog."""
        self.write({"url": False})
        return {"type": "ir.actions.act_window_close"}
