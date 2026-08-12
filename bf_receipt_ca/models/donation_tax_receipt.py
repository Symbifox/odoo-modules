# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class DonationTaxReceipt(models.Model):
    """Make the OCA tax receipt CRA/Revenu-Québec compliant: advantage &
    eligible amount, in-kind (FMV + appraiser), and a void/reissue lifecycle."""

    _inherit = "donation.tax.receipt"

    advantage_amount = fields.Monetary(
        string="Montant de l'avantage",
        currency_field="currency_id",
        default=0.0,
        help="Valeur de tout avantage reçu par le donateur en contrepartie du "
        "don (ex. repas, billet). Réduit le montant admissible.",
    )
    advantage_description = fields.Char(string="Description de l'avantage")
    eligible_amount = fields.Monetary(
        string="Montant admissible",
        currency_field="currency_id",
        compute="_compute_eligible_amount",
        store=True,
        help="Montant admissible du don aux fins de l'impôt = montant reçu "
        "moins la valeur de l'avantage.",
    )
    in_kind = fields.Boolean(string="Don en nature")
    property_description = fields.Text(string="Description du bien")
    fmv = fields.Monetary(
        string="Juste valeur marchande", currency_field="currency_id"
    )
    appraiser_name = fields.Char(string="Nom de l'évaluateur")
    appraiser_address = fields.Text(string="Adresse de l'évaluateur")

    state = fields.Selection(
        [("issued", "Émis"), ("cancelled", "Annulé")],
        string="État",
        default="issued",
        required=True,
        tracking=True,
    )
    cancel_reason = fields.Char(string="Motif d'annulation")
    replaced_receipt_id = fields.Many2one(
        "donation.tax.receipt", string="Remplace le reçu", readonly=True, copy=False
    )
    replacement_receipt_id = fields.Many2one(
        "donation.tax.receipt", string="Remplacé par", readonly=True, copy=False
    )

    @api.depends("amount", "advantage_amount")
    def _compute_eligible_amount(self):
        for receipt in self:
            # Never negative: the eligible amount for tax purposes is the gift
            # received less the advantage, floored at zero.
            receipt.eligible_amount = max(
                0.0, (receipt.amount or 0.0) - (receipt.advantage_amount or 0.0)
            )

    @api.constrains("amount", "advantage_amount")
    def _check_advantage_amount(self):
        for receipt in self:
            if receipt.advantage_amount and receipt.advantage_amount > (
                receipt.amount or 0.0
            ):
                raise ValidationError(
                    _(
                        "La valeur de l'avantage (%(adv)s) ne peut excéder le "
                        "montant du don (%(amount)s) sur le reçu %(num)s.",
                        adv=receipt.advantage_amount,
                        amount=receipt.amount,
                        num=receipt.number or _("(nouveau)"),
                    )
                )

    def bf_receipt_email_layout(self):
        """Email layout for receipt notifications. Uses Blue Fox's branded
        transactional layout when ``bluefox_branding`` is installed (optional
        dependency), otherwise Odoo's stock light layout — same method as the
        other Blue Fox modules (invoices, quotes, contracts)."""
        if self.env.ref("bluefox_branding.bf_mail_layout", raise_if_not_found=False):
            return "bluefox_branding.bf_mail_layout"
        return "mail.mail_notification_light"

    def action_send_tax_receipt(self):
        action = super().action_send_tax_receipt()
        if isinstance(action, dict) and isinstance(action.get("context"), dict):
            action["context"]["default_email_layout_xmlid"] = (
                self.bf_receipt_email_layout()
            )
        return action

    def action_void(self):
        for receipt in self:
            if receipt.state == "cancelled":
                raise UserError(
                    _("Le reçu %s est déjà annulé.") % receipt.number
                )
            receipt.state = "cancelled"
            receipt.message_post(
                body=_(
                    "Reçu annulé. Motif : %s"
                )
                % (receipt.cancel_reason or _("non précisé"))
            )

    def action_reissue(self):
        """Cancel this receipt and create a replacement with a fresh serial
        number, keeping the audit chain (the ARC requires cancelled receipts to
        be retained)."""
        self.ensure_one()
        if self.state != "cancelled":
            self.state = "cancelled"
        new_receipt = self.copy(
            {
                "number": _("New"),
                "state": "issued",
                "replaced_receipt_id": self.id,
                "date": fields.Date.context_today(self),
                "print_date": False,
                "cancel_reason": False,
            }
        )
        self.replacement_receipt_id = new_receipt.id
        self.message_post(
            body=_("Remplacé par le reçu %s.") % new_receipt.number
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "donation.tax.receipt",
            "res_id": new_receipt.id,
            "view_mode": "form",
            "target": "current",
        }
