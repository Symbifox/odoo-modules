from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.bf_membership.models.membership import keep_trace

#: Politique CSP-M05 de l'ARC (cotisations de membre) : un avantage qui ne
#: dépasse pas le MOINDRE de 75 $ et de 10 % de la cotisation est négligé.
DE_MINIMIS_AMOUNT = 75.0
DE_MINIMIS_RATE = 0.10
#: Au-delà de 80 % de la cotisation, l'ARC ne voit plus d'intention de donner :
#: aucun reçu.
NO_GIFT_RATE = 0.80


def csp_m05_eligible(amount, advantage, currency):
    """(montant admissible, avantage négligé) d'une cotisation de `amount` qui
    donne au membre un avantage d'une juste valeur marchande de `advantage`.

    * avantage nul ou négligeable (au plus le moindre de 75 $ et de 10 % de la
      cotisation) : la cotisation entière, et l'avantage n'est pas déduit ;
    * avantage de plus de 80 % de la cotisation : aucun reçu (0) ;
    * entre les deux : la cotisation moins l'avantage (reçu partiel).

    Les bornes sont comprises du côté favorable au membre : un avantage égal au
    seuil de minimis est négligé, un avantage égal à 80 % donne encore un reçu.
    Les comparaisons passent par l'arrondi de la devise, jamais par `<=` sur
    des flottants : 10 % de 70,00 $ vaut 7,000000000000001 en binaire.
    """
    if currency.compare_amounts(amount, 0.0) <= 0:
        return 0.0, False
    advantage = max(advantage or 0.0, 0.0)
    de_minimis = min(DE_MINIMIS_AMOUNT, currency.round(amount * DE_MINIMIS_RATE))
    if currency.compare_amounts(advantage, de_minimis) <= 0:
        return currency.round(amount), bool(advantage)
    if currency.compare_amounts(advantage, currency.round(amount * NO_GIFT_RATE)) > 0:
        return 0.0, False
    return currency.round(amount - advantage), False


class MembershipType(models.Model):
    """La catégorie sait ce qu'elle facture et ce qu'elle reçoit en reçu fiscal.

    L'avantage se décrit sur la catégorie, pas sur l'adhésion : c'est la
    catégorie qui donne le tarif réduit aux activités ou la revue, et l'ARC
    demande la juste valeur marchande de ce que le membre reçoit, la même pour
    tous les membres de la catégorie.
    """

    _inherit = "bf.membership.type"

    product_id = fields.Many2one(
        "product.product", string="Article de cotisation", tracking=True,
        domain="[('type', '=', 'service'), ('company_id', 'in', (company_id, False))]",
        help="L'article porté par la facture : il décide du compte de revenus et "
             "des taxes. Créé à la première facture s'il manque, sans taxe.",
    )
    receipt_eligible = fields.Boolean(
        string="Reçu fiscal", tracking=True,
        help="Pour un organisme de bienfaisance enregistré : la cotisation donne "
             "un reçu officiel pour sa part admissible. Un OBNL qui n'est pas "
             "enregistré auprès de l'ARC ne délivre aucun reçu.",
    )
    advantage_amount = fields.Monetary(
        string="Valeur de l'avantage", tracking=True,
        help="Juste valeur marchande de ce que le membre reçoit en échange de sa "
             "cotisation (revue, rabais, entrées). Zéro si l'adhésion ne donne "
             "que le droit de vote et les avis d'assemblée.",
    )
    advantage_description = fields.Char(
        string="Nature de l'avantage",
        help="Imprimée sur le reçu, comme l'exige l'article 3501 du Règlement.",
    )
    receipt_eligible_amount = fields.Monetary(
        string="Montant admissible au reçu", compute="_compute_receipt_eligible_amount",
        help="Pour la cotisation de la catégorie, selon la politique CSP-M05 de l'ARC.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        return super(MembershipType, keep_trace(self)).create(vals_list)

    def write(self, vals):
        """La catégorie dit ce qui se facture et ce qui se reçoit : ses changements
        gardent leur trace, quel que soit le contexte de l'appel."""
        return super(MembershipType, keep_trace(self)).write(vals)

    @api.depends("fee", "advantage_amount", "receipt_eligible", "currency_id")
    def _compute_receipt_eligible_amount(self):
        for rec in self:
            rec.receipt_eligible_amount = rec._eligible_amount(rec.fee)[0] if rec.receipt_eligible else 0.0

    @api.constrains("advantage_amount", "advantage_description", "receipt_eligible")
    def _check_advantage(self):
        for rec in self:
            if rec.advantage_amount < 0:
                raise ValidationError(_("La valeur de l'avantage ne peut pas être négative."))
            if rec.receipt_eligible and rec.advantage_amount and not rec.advantage_description:
                raise ValidationError(_(
                    "Décrivez l'avantage de la catégorie « %s » : le reçu doit le nommer.", rec.name))

    def _eligible_amount(self, amount):
        """(montant admissible, avantage négligé) pour une cotisation de `amount`."""
        self.ensure_one()
        return csp_m05_eligible(amount, self.advantage_amount, self.currency_id or self.env.company.currency_id)

    def _membership_product(self):
        """L'article de la cotisation, créé à la première facture s'il manque.

        🔴 L'article créé ici ne porte AUCUNE taxe. La cotisation d'un organisme
        de bienfaisance ou d'un OBNL qui ne donne que le droit de vote et les
        avis est le plus souvent exonérée ; une cotisation qui donne des
        avantages importants peut être taxable. Le module ne tranche pas : la
        taxe par défaut de la société facturerait de la TPS et de la TVQ à des
        membres qui n'en doivent pas, ce qui est pire à corriger qu'un oubli.
        L'organisme pose les taxes sur l'article s'il y a lieu (README).

        Créé en superutilisateur : l'agent qui facture n'a pas le droit de créer
        des articles, et il n'en choisit aucun ; la catégorie décide.
        """
        self.ensure_one()
        if self.product_id:
            return self.product_id
        product = self.env["product.product"].sudo().with_company(self.company_id).create({
            "name": _("Cotisation : %s", self.name),
            "type": "service",
            "sale_ok": True,
            "purchase_ok": False,
            "list_price": self.fee,
            "default_code": self.code or False,
            "company_id": self.company_id.id,
            "taxes_id": [fields.Command.clear()],
            "supplier_taxes_id": [fields.Command.clear()],
        })
        self.sudo().product_id = product
        return product
