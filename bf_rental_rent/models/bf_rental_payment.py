"""Un versement de loyer, imputé sur un terme."""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.misc import format_amount, format_date


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

    # Sans nom, la fiche s'affichait « bf.rental.payment,4 ». Même
    # garde que le terme : le locataire lit ses versements au portail, le nom
    # ne prend donc que le montant, la date et la référence de SON bail.
    @api.depends("amount", "date", "currency_id", "lease_id.name")
    @api.depends_context("lang")
    def _compute_display_name(self):
        for payment in self:
            if not (payment.date and payment.lease_id):
                payment.display_name = payment.lease_id.name or False
                continue
            payment.display_name = _(
                "Versement de %(amount)s reçu le %(date)s · %(lease)s",
                amount=format_amount(
                    payment.env, payment.amount, payment.currency_id),
                date=format_date(payment.env, payment.date),
                lease=payment.lease_id.name,
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
