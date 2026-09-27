"""Un versement de loyer, imputé sur un terme."""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BfRentalPayment(models.Model):
    _name = "bf.rental.payment"
    _description = "Versement de loyer"
    _order = "date desc, id desc"

    term_id = fields.Many2one(
        "bf.rental.term", string="Terme", required=True,
        ondelete="cascade", index=True,
    )
    lease_id = fields.Many2one(
        related="term_id.lease_id", store=True, readonly=True, index=True,
    )
    company_id = fields.Many2one(
        related="term_id.company_id", store=True, readonly=True,
    )
    currency_id = fields.Many2one(
        related="term_id.currency_id", readonly=True,
    )
    date = fields.Date(
        string="Reçu le", required=True, default=fields.Date.context_today,
    )
    amount = fields.Monetary(string="Montant", required=True)
    mode = fields.Selection(
        [("cash", "Argent comptant"), ("cheque", "Chèque"),
         ("transfer", "Virement"), ("other", "Autre")],
        string="Mode",
    )
    receipt_given = fields.Boolean(
        string="Reçu remis",
        help="⚠️ Art. 1564 C.c.Q. : le locataire qui paie EN ARGENT COMPTANT a "
             "droit à un reçu. Ce n'est pas une courtoisie, et le formulaire "
             "de bail le rappelle dans ses mentions.",
    )

    @api.constrains("amount")
    def _check_the_amount_is_positive(self):
        for payment in self:
            if payment.amount <= 0:
                raise ValidationError(_(
                    "Un versement porte un montant positif. Une correction se "
                    "fait en modifiant le versement, pas en en créant un "
                    "négatif qui ferait mentir l'historique."
                ))
