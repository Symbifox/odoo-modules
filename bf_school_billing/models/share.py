from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

#: Moved only by the billing, and what a payer is born with: who was invoiced, on which invoice.
SHARE_DEFAULTS = {"invoice_id": False}
SHARE_SYSTEM_FIELDS = set(SHARE_DEFAULTS)


class BillingShare(models.Model):
    """Who pays a contract, and how much.

    Shared custody is two family shares in percentages (50/50, 60/40…). A bursary, or a
    third party such as an employer, pays a fixed amount first; the families split the rest.
    """

    _name = "bf.school.billing.share"
    _description = "Payer of a contract"
    _order = "contract_id, kind desc, sequence, id"

    contract_id = fields.Many2one("bf.school.contract", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="contract_id.company_id", store=True)
    currency_id = fields.Many2one(related="contract_id.currency_id")
    sequence = fields.Integer(default=10)
    partner_id = fields.Many2one("res.partner", "Payer", required=True, ondelete="restrict", index=True)
    kind = fields.Selection(
        [("family", "Family"), ("bursary", "Bursary or third party")],
        required=True, default="family")
    percent = fields.Float("Share (%)", help="Family: its share of what the bursaries leave.")
    amount = fields.Monetary("Fixed amount", help="Bursary or third party: the amount it pays.")
    billed_amount = fields.Monetary("Billed", compute="_compute_billed_amount")
    invoice_id = fields.Many2one("account.move", "Invoice", readonly=True, copy=False, index=True)

    # Read in sudo: the office has no access to the invoices themselves.
    invoice_name = fields.Char("Invoice number", compute="_compute_invoice_status")
    invoice_paid = fields.Monetary("Paid", compute="_compute_invoice_status")
    invoice_residual = fields.Monetary("Left to pay", compute="_compute_invoice_status")
    invoice_next_due = fields.Date("Next due date", compute="_compute_invoice_status")
    invoice_overdue = fields.Boolean("Overdue", compute="_compute_invoice_status")

    _sql_constraints = [
        ("contract_partner_unique", "UNIQUE(contract_id, partner_id)",
         "A payer appears once on a contract."),
    ]

    @api.constrains("kind", "percent", "amount")
    def _check_share(self):
        for share in self:
            if share.kind == "family" and not 0 < share.percent <= 100:
                raise ValidationError(_("A family share is between 0 and 100 %."))
            if share.kind == "bursary" and share.amount <= 0:
                raise ValidationError(_("A bursary or a third party pays a positive amount."))

    @api.depends("kind", "percent", "amount", "contract_id.total_price", "contract_id.share_ids")
    def _compute_billed_amount(self):
        for contract, shares in self.grouped("contract_id").items():
            amounts = contract._school_share_amounts(raise_if_invalid=False)
            for share in shares:
                share.billed_amount = amounts.get(share.id, 0.0)

    def _compute_invoice_status(self):
        today = fields.Date.context_today(self)
        for share in self:
            invoice = share.sudo().invoice_id
            live = invoice and invoice.state == "posted"
            share.invoice_name = invoice.name if invoice else False
            share.invoice_paid = share.sudo()._school_paid() if live else 0.0
            share.invoice_residual = invoice.amount_residual if live else 0.0
            terms = invoice.line_ids.filtered(
                lambda l: l.display_type == "payment_term" and not l.reconciled) if live else []
            dates = sorted(terms.mapped("date_maturity")) if live else []
            share.invoice_next_due = dates[0] if dates else False
            share.invoice_overdue = bool(dates) and dates[0] < today

    def _school_paid(self):
        """Paid on this share's invoice, net of what was paid back."""
        self.ensure_one()
        refunds = self.env["account.move"].search([
            ("reversed_entry_id", "=", self.invoice_id.id), ("move_type", "=", "out_refund"),
            ("state", "=", "posted")])
        return (self.invoice_id | refunds)._school_paid_amount()

    def _school_billed(self):
        """Shares whose invoice is live (a cancelled invoice is billed again)."""
        return self.filtered(lambda s: s.sudo().invoice_id.state in ("draft", "posted"))

    @api.model_create_multi
    def create(self, vals_list):
        # 🔴 Guarded in write only: at creation, a payer on another family's live invoice was
        # refused by luck (its contract looked billed), and one on a cancelled invoice passed.
        # Written explicitly, because Odoo adds `default_invoice_id` from the
        # context and the user's own ir.default AFTER this check; in sudo too, where the payers
        # born at the signature took the signer's own ir.default: another family's invoice, then
        # credited at the termination.
        for vals in vals_list:
            if not self.env.su and any(vals.get(f) and vals[f] != v for f, v in SHARE_DEFAULTS.items()):
                raise UserError(_("The invoice of a payer is set by the billing."))
            for field, value in SHARE_DEFAULTS.items():
                vals.setdefault(field, value)
        shares = super().create(vals_list)
        shares.contract_id._school_check_shares_editable()
        return shares

    def write(self, vals):
        if not self.env.su:
            if SHARE_SYSTEM_FIELDS & set(vals):
                raise UserError(_("The invoice of a payer is set by the billing."))
            self.contract_id._school_check_shares_editable()
        result = super().write(vals)
        # 🔴 Where it went too: a payer moved from a draft changed the payers of a billed
        # contract.
        if not self.env.su and "contract_id" in vals:
            self.contract_id._school_check_shares_editable()
        return result

    def unlink(self):
        self.contract_id._school_check_shares_editable()
        return super().unlink()
