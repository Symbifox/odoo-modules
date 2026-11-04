from odoo import fields, models


class PurchaseOrderLine(models.Model):
    """Trois aides PURES par ligne de commande : compte, date, montant.

    Aucune écriture, aucun accès aux budgets : testables seules, et partagées
    entre le calcul des montants et le bouton qui ouvre les commandes.
    """

    _inherit = "purchase.order.line"

    def _bf_budget_account(self):
        """Le compte de charge où la facture de cette ligne tombera.

        Le même chemin que la facturation d'Odoo : le compte de charge du
        produit, position fiscale de la commande comprise. C'est lui qui décide
        du poste budgétaire — jamais un compte deviné depuis le poste.
        """
        self.ensure_one()
        if not self.product_id:
            return self.env["account.account"]
        accounts = self.product_id.product_tmpl_id.with_company(
            self.company_id
        ).get_product_accounts(fiscal_pos=self.order_id.fiscal_position_id)
        return accounts.get("expense") or self.env["account.account"]

    def _bf_budget_date(self):
        """La date à laquelle le plan attend cette dépense.

        La date prévue de la ligne, lue dans le fuseau de la personne — c'est
        celle que l'écran affiche. À défaut (ligne d'acompte), la commande fait
        foi : son approbation, sinon sa date de commande.
        """
        self.ensure_one()
        moment = (
            self.date_planned
            or self.order_id.date_approve
            or self.order_id.date_order
        )
        return fields.Date.context_today(self, moment) if moment else False

    def _bf_budget_convert(self, amount, currency, company):
        """Un montant de la devise de la commande vers celle du budget, à la
        date prévue : UNE conversion, une seule date, pour que le reste à
        facturer d'une commande entièrement facturée tombe à zéro au lieu de
        porter l'écart entre deux taux."""
        self.ensure_one()
        if self.currency_id and currency and self.currency_id != currency:
            amount = self.currency_id._convert(
                amount, currency, company,
                self._bf_budget_date() or fields.Date.context_today(self),
            )
        return amount

    def _bf_budget_amount(self, currency, company):
        """Le sous-total HT, converti dans la devise du budget à la date prévue.

        Hors taxes parce que le réalisé l'est aussi : les taxes récupérables
        vont à leurs propres comptes, pas à la charge.
        """
        self.ensure_one()
        return self._bf_budget_convert(self.price_subtotal, currency, company)
