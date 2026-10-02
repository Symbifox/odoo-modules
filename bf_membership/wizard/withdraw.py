from odoo import fields, models


class MembershipWithdraw(models.TransientModel):
    """Le départ d'un membre se date et se motive. Le contact reste au registre."""

    _name = "bf.membership.withdraw"
    _description = "Retrait d'une adhésion"

    membership_ids = fields.Many2many("bf.membership", string="Adhésions", required=True)
    date = fields.Date(string="Date du retrait", required=True, default=fields.Date.context_today)
    reason = fields.Text(string="Motif", required=True)

    def action_confirm(self):
        self.membership_ids._withdraw(self.date, self.reason)
        return {"type": "ir.actions.act_window_close"}
