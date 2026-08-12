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
    _description = "Prolonger l'échéance d'un transfert"

    transfer_id = fields.Many2one(
        "secure.transfer", string="Transfert", readonly=True, required=True,
    )
    transfer_state = fields.Selection(
        related="transfer_id.state", string="État actuel",
    )
    current_expiry = fields.Datetime(
        related="transfer_id.expiry_date", string="Échéance actuelle",
    )
    days = fields.Selection(
        selection="_selection_days", string="Nouvelle durée", required=True,
        help="Durée totale de disponibilité, comptée depuis l'envoi.",
    )
    new_expiry = fields.Datetime(
        string="Nouvelle échéance", compute="_compute_new_expiry",
    )

    def _selection_days(self):
        """Only the tiers this transfer could still move UP to. A Selection
        computed per record: an Integer would have let a manager type a date
        the storage lifecycle will not honour."""
        transfer = self.env["secure.transfer"].browse(
            self.env.context.get("default_transfer_id") or []).exists()
        if not transfer:
            return []
        return [(str(d), _("%s jours", d) if d > 1 else _("1 jour"))
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
            raise UserError(_("Aucun transfert sélectionné."))
        if not self.env.user.has_group(
                "bf_securetransfer.group_securetransfer_manager"):
            raise UserError(_(
                "Action réservée aux gestionnaires du transfert sécurisé."
            ))
        choices = transfer._extension_choices()
        if not choices:
            raise UserError(_(
                "Ce transfert est déjà à la durée maximale offerte par "
                "« %(brand)s » (%(days)s jours). Créez un nouvel envoi si le "
                "destinataire a besoin de plus de temps.",
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
