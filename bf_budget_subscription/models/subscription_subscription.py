from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

# ⚠️ La table des cycles se LIT chez `bf_subscription`, elle ne se recopie pas.
# Deux tables pour un même fait finissent toujours par diverger, et celle-ci
# décide de la date de chaque échéance : une divergence produirait un calendrier
# qui contredit la « prochaine facturation » affichée sur la fiche elle-même.
from odoo.addons.bf_subscription.models.subscription_subscription import CYCLE_MONTHS


class Subscription(models.Model):
    _inherit = "subscription.subscription"

    budget_position_id = fields.Many2one(
        "bf.budget.position",
        string="Poste budgétaire",
        ondelete="set null",
        index=True,
        domain="[('budget_type', '=', 'expense'), ('company_id', '=', company_id)]",
        help="Le poste dont les budgets comptent cet abonnement. Se déduit des "
        "factures fournisseur par l'assistant de rattachement.",
    )
    budget_has_calendar = fields.Boolean(
        string="Échéancier connu",
        compute="_compute_budget_has_calendar",
        store=True,
        help="Faux pour un abonnement à la demande : il dépense sans échéancier.",
    )

    @api.depends("cycle", "start_date")
    def _compute_budget_has_calendar(self):
        for sub in self:
            sub.budget_has_calendar = bool(
                sub.start_date and (sub.cycle in CYCLE_MONTHS or sub.cycle == "one_time")
            )

    def _budget_horizon_end(self):
        """La date après laquelle cet abonnement ne coûte plus rien.

        ⚠️ Un abonnement résilié a bel et bien coûté quelque chose avant de
        l'être, et ce coût est dans les livres. L'ignorer ferait un théorique
        trop bas, donc une dérive trop haute, donc de fausses alertes. Quand la
        date de fin n'est pas saisie, la dernière facture reçue fait foi : c'est
        la seule borne que la base connaisse vraiment.
        """
        self.ensure_one()
        if self.end_date:
            return self.end_date
        if self.state in ("cancelled", "expired"):
            return self.last_vendor_bill_date or self.start_date
        return None

    def _budget_occurrences(self, date_from, date_to):
        """Les échéances de cet abonnement dans une fenêtre, en aide PURE.

        Rend une liste de couples (date, montant du cycle), dans la devise de
        l'abonnement. Aucune écriture, aucun accès aux budgets : testable seule.
        """
        self.ensure_one()
        if not self.budget_has_calendar or not self.start_date:
            return []
        horizon = self._budget_horizon_end()
        stop = min(date_to, horizon) if horizon else date_to
        if stop < self.start_date:
            return []
        if self.cycle == "one_time":
            if date_from <= self.start_date <= stop:
                return [(self.start_date, self.cycle_amount)]
            return []
        months = CYCLE_MONTHS.get(self.cycle)
        if not months:
            return []
        occurrences = []
        moment = self.start_date
        # Garde-fou identique à celui de `bf_subscription` : une date de début
        # aberrante ne doit pas faire boucler le calcul d'un budget.
        for _step in range(600):
            if moment > stop:
                break
            if moment >= date_from:
                occurrences.append((moment, self.cycle_amount))
            moment += relativedelta(months=months)
        return occurrences

    def _budget_amount_between(self, date_from, date_to, currency, company):
        """Le total dû dans la fenêtre, converti dans la devise du budget."""
        self.ensure_one()
        total = 0.0
        for moment, amount in self._budget_occurrences(date_from, date_to):
            if self.currency_id and currency and self.currency_id != currency:
                amount = self.currency_id._convert(amount, currency, company, moment)
            total += amount
        return total

    def action_view_budget_position(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.budget.position",
            "res_id": self.budget_position_id.id,
            "view_mode": "form",
        }
