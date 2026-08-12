# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class DonationDonation(models.Model):
    """Capture the advantage and (for in-kind gifts) the property description
    so the generated tax receipt is CRA-compliant."""

    _inherit = "donation.donation"

    advantage_amount = fields.Monetary(
        string="Montant de l'avantage",
        currency_field="company_currency_id",
        default=0.0,
        tracking=True,
        help="Valeur de tout avantage reçu par le donateur (repas, billet…). "
        "Sera reportée sur le reçu et réduira le montant admissible.",
    )
    advantage_description = fields.Char(string="Description de l'avantage")
    receipt_property_description = fields.Text(
        string="Description du bien (don en nature)",
        help="Description du bien donné, reportée sur le reçu pour les dons "
        "en nature.",
    )

    @api.constrains("advantage_amount", "tax_receipt_total")
    def _check_advantage_not_exceeding(self):
        for donation in self:
            if (
                donation.advantage_amount
                and donation.tax_receipt_total
                and donation.advantage_amount > donation.tax_receipt_total
            ):
                raise ValidationError(
                    _(
                        "La valeur de l'avantage (%(adv)s) ne peut excéder le "
                        "montant admissible du don (%(elig)s).",
                        adv=donation.advantage_amount,
                        elig=donation.tax_receipt_total,
                    )
                )

    def _prepare_each_tax_receipt(self):
        vals = super()._prepare_each_tax_receipt()
        in_kind = bool(self.line_ids) and all(self.line_ids.mapped("in_kind"))
        vals.update(
            {
                "advantage_amount": self.advantage_amount,
                "advantage_description": self.advantage_description,
                "in_kind": in_kind,
                "property_description": self.receipt_property_description,
            }
        )
        if in_kind:
            vals["fmv"] = self.tax_receipt_total
        return vals
