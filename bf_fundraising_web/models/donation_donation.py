# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DonationDonation(models.Model):
    _inherit = "donation.donation"

    is_web_donation = fields.Boolean(
        string="Don en ligne",
        readonly=True,
        copy=False,
        help="Don créé via le formulaire de don public du site web.",
    )

    @api.model
    def _get_web_donation_product(self):
        """The product used for web gifts: the company's credit-transfer
        donation product, else any receipt-eligible donation product."""
        company = self.env.company
        product = company.donation_credit_transfer_product_id
        if not product:
            product = self.env["product.product"].search(
                [("donation_type", "!=", False), ("tax_receipt_ok", "=", True)],
                limit=1,
            )
        return product

    @api.model
    def create_web_donation(self, partner_id, amount, fund_id=False, campaign_id=False):
        """Create a draft donation from the public web form. Staff validate it
        later (which posts the entry and issues + emails the receipt)."""
        product = self._get_web_donation_product()
        if not product:
            raise UserError(
                _(
                    "Aucun produit de don n'est configuré. Configurez le produit "
                    "de don sur la société avant d'activer le formulaire web."
                )
            )
        vals = {
            "partner_id": partner_id,
            "donation_date": fields.Date.context_today(self),
            "is_web_donation": True,
            "tax_receipt_option": "each",
            "line_ids": [
                Command.create(
                    {
                        "product_id": product.id,
                        "quantity": 1,
                        "unit_price": amount,
                        "fund_id": fund_id or False,
                    }
                )
            ],
        }
        if campaign_id:
            vals["campaign_id"] = campaign_id
        return self.create(vals)

    def validate(self):
        res = super().validate()
        template = self.env.ref(
            "donation_base.tax_receipt_email_template", raise_if_not_found=False
        )
        for donation in self:
            if (
                donation.is_web_donation
                and donation.tax_receipt_id
                and donation.partner_id.email
                and template
            ):
                # Queue the receipt email (force_send=False) and never let a mail
                # failure roll back a validated gift — the gift + receipt are the
                # source of truth; the email is best-effort.
                try:
                    template.send_mail(
                        donation.tax_receipt_id.id,
                        force_send=False,
                        email_layout_xmlid=donation.tax_receipt_id.bf_receipt_email_layout(),
                    )
                except Exception:  # noqa: BLE001 - email must not break validation
                    _logger.warning(
                        "Échec de l'envoi du reçu web pour le don %s",
                        donation.display_name,
                        exc_info=True,
                    )
        return res
