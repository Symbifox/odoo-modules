import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    subscription_id = fields.Many2one(
        'subscription.subscription',
        string="Subscription",
        copy=False, index=True, ondelete='set null', tracking=True,
        help="Subscription this invoice is linked to. For a vendor bill, "
             "it is the underlying contract. For a customer invoice, it "
             "is the matching rebill.",
    )

    # ------------------------------------------------------------------
    # Auto-suggest subscription on bill creation
    # ------------------------------------------------------------------

    def _suggest_subscription(self):
        """Find a unique active subscription matching this vendor bill.

        Match rules (vendor bills only):
        - partner_id matches subscription.vendor_id
        - amount_total is within ±10% of cycle_amount
        - subscription state is 'active'
        - currency matches
        - exactly ONE candidate (otherwise stay manual)

        Customer invoices are not auto-matched (they're created by the rebill wizard).
        """
        self.ensure_one()
        if self.subscription_id or self.move_type not in ('in_invoice', 'in_refund'):
            return False
        if not self.partner_id or not self.amount_total:
            return False
        Subscription = self.env['subscription.subscription'].sudo()
        partner_ids = [self.partner_id.id]
        if self.partner_id.commercial_partner_id and self.partner_id.commercial_partner_id.id != self.partner_id.id:
            partner_ids.append(self.partner_id.commercial_partner_id.id)
        # Match within the user's accessible companies, biased toward the bill's company.
        allowed_company_ids = list(self.env.companies.ids) or [self.company_id.id]
        candidates = Subscription.search([
            ('vendor_id', 'in', partner_ids),
            ('state', '=', 'active'),
            ('company_id', 'in', allowed_company_ids),
        ])
        if not candidates:
            return False
        target = self.amount_total
        matches = candidates.filtered(
            lambda s: s.cycle_amount
            and abs(s.cycle_amount - target) <= s.cycle_amount * 0.10
        )
        if len(matches) == 1:
            return matches.id
        return False

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        for move in moves:
            if move.move_type not in ('in_invoice', 'in_refund'):
                continue
            if not move.subscription_id:
                suggested = move._suggest_subscription()
                if suggested:
                    move.subscription_id = suggested
                    _logger.info(
                        "bf_subscription: auto-linked bill %s to subscription %s",
                        move.name or move.id, suggested,
                    )
            if move.subscription_id and move.move_type == 'in_invoice':
                move.subscription_id._maybe_alert_price_increase(move)
        return moves

    def write(self, vals):
        res = super().write(vals)
        # Manual linking: alert on price increase for newly-linked vendor bills.
        if 'subscription_id' in vals and vals.get('subscription_id'):
            for move in self:
                if move.move_type == 'in_invoice' and move.subscription_id:
                    move.subscription_id._maybe_alert_price_increase(move)
        return res

    def action_open_rebill_wizard(self):
        self.ensure_one()
        if not self.subscription_id:
            raise UserError(_("No subscription linked to this bill."))
        if not self.subscription_id.rebilled:
            raise UserError(_(
                "Rebilling is not turned on for the subscription \"%s\"."
            ) % self.subscription_id.name)
        return {
            'type': 'ir.actions.act_window',
            'name': _("Rebill to client"),
            'res_model': 'subscription.rebill.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_subscription_id': self.subscription_id.id,
                'default_vendor_bill_id': self.id,
            },
        }
