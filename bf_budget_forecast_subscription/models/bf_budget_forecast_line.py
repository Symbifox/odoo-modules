from odoo import api, fields, models


class BfBudgetForecastLine(models.Model):
    """L'amorce cesse d'être plate dès qu'un calendrier existe."""

    _inherit = "bf.budget.forecast.line"

    subscription_ids = fields.Many2many(
        "subscription.subscription",
        string="Abonnements du poste",
        compute="_compute_forecast_subscriptions",
    )

    @api.depends("position_id", "company_id")
    def _compute_forecast_subscriptions(self):
        Subscription = self.env["subscription.subscription"]
        for line in self:
            if not line.position_id:
                line.subscription_ids = Subscription
                continue
            line.subscription_ids = Subscription.sudo().search(
                [
                    ("budget_position_id", "=", line.position_id.id),
                    ("company_id", "in", (line.company_id.id, False)),
                ]
            )

    def _dated_amount(self, date_from, date_to):
        """Ce que le calendrier des abonnements explique sur une fenêtre."""
        self.ensure_one()
        if not self.subscription_ids:
            return 0.0
        currency = self.currency_id
        company = self.forecast_id.company_id
        return sum(
            sub._budget_amount_between(date_from, date_to, currency, company)
            for sub in self.subscription_ids
        )

    def _seed_value_for(self, period):
        """Le daté à sa date, la moyenne pour le reste — et rien compté deux fois.

        🔴 La soustraction est le cœur du pont. Le réel des mois clos contient
        DÉJÀ les échéances d'abonnement de ces mois-là. Moyenner ce réel brut
        puis y rajouter les échéances datées du mois à prévoir compterait le
        récurrent deux fois, et gonflerait la prévision de tout le poids de la
        partie la mieux connue du budget.
        """
        self.ensure_one()
        if not self.subscription_ids:
            return super()._seed_value_for(period)
        clos = self.period_ids.filtered("is_closed")
        if not clos:
            # Aucun historique : le calendrier est tout ce qu'on sait.
            return self._dated_amount(period.date_start, period.date_end)
        reste = 0.0
        for ferme in clos:
            date = ferme.amount_actual - self._dated_amount(ferme.date_start, ferme.date_end)
            reste += date
        reste_moyen = max(0.0, reste / len(clos))
        return self._dated_amount(period.date_start, period.date_end) + reste_moyen
