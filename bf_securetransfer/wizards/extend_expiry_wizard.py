"""Move a transfer's deadline out to another retention tier.

The expiry date is read-only on the form: every change goes through here, so
it lands in the Loi 25 trail like everything else the operator does. The
durations offered are the brand's own grid, never a free date — see
``secure.transfer.extend_expiry`` for why (the S3 lifecycle net is posted from
that same grid).
"""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SecureTransferExtendWizard(models.TransientModel):
    _name = "secure.transfer.extend.wizard"
    _description = "Extend a transfer's expiry"

    transfer_id = fields.Many2one(
        "secure.transfer", string="Transfer", readonly=True, required=True,
    )
    transfer_state = fields.Selection(
        related="transfer_id.state", string="Current state",
    )
    current_expiry = fields.Datetime(
        related="transfer_id.expiry_date", string="Current expiry",
    )
    days = fields.Selection(
        selection="_selection_days", string="New duration", required=True,
        help="Total availability, counted from the send.",
    )
    new_expiry = fields.Datetime(
        string="New expiry", compute="_compute_new_expiry",
    )

    def _selection_days(self):
        """Only the tiers this transfer could still move UP to. A Selection
        computed per record: an Integer would have let a manager type a date
        the storage lifecycle will not honour."""
        transfer = self.env["secure.transfer"].browse(
            self.env.context.get("default_transfer_id") or []).exists()
        if not transfer:
            return []
        return [(str(d), _("%s days", d) if d > 1 else _("1 day"))
                for d in transfer._extension_choices()]

    @api.depends("transfer_id", "days")
    def _compute_new_expiry(self):
        for wiz in self:
            base = (wiz.transfer_id.finalized_at
                    or wiz.transfer_id.create_date)
            if not base or not wiz.days:
                wiz.new_expiry = False
                continue
            wiz.new_expiry = base + timedelta(days=int(wiz.days))

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
                "bf_securetransfer.group_securetransfer_manager"):
            raise UserError(_(
                "Action restricted to secure transfer managers."
            ))
        choices = transfer._extension_choices()
        if not choices:
            raise UserError(_(
                "This transfer is already at the longest duration "
                "\"%(brand)s\" offers (%(days)s days). Create a new send "
                "if the recipient needs more time.",
                brand=transfer.brand_id.display_name,
                days=transfer.retention_days,
            ))
        res["transfer_id"] = transfer.id
        res.setdefault("days", str(choices[0]))
        return res

    def action_apply(self):
        self.ensure_one()
        self.transfer_id.extend_expiry(int(self.days))
        return {"type": "ir.actions.act_window_close"}
