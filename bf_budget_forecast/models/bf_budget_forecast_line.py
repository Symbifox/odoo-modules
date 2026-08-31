from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class BfBudgetForecastLine(models.Model):
    """Une ligne de prévision : un poste, sur tout l'horizon.

    Elle réutilise `bf.budget.position` : le budget et la prévision se lisent sur
    le MÊME axe, sans quoi on ne pourrait comparer ni l'un ni l'autre à quoi que
    ce soit.
    """

    _name = "bf.budget.forecast.line"
    _description = "Ligne de prévision"
    _order = "forecast_id, position_id"

    forecast_id = fields.Many2one(
        "bf.budget.forecast", required=True, ondelete="cascade", index=True
    )
    position_id = fields.Many2one(
        "bf.budget.position",
        string="Poste",
        required=True,
        ondelete="restrict",
        index=True,
        domain="[('budget_type', '=', 'expense'), ('company_id', '=', company_id)]",
    )
    company_id = fields.Many2one(related="forecast_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="forecast_id.currency_id")
    state = fields.Selection(related="forecast_id.state", store=True)
    name = fields.Char(related="position_id.display_name", string="Poste")

    period_ids = fields.One2many(
        "bf.budget.forecast.period", "line_id", string="Mois", copy=True
    )

    amount_actual = fields.Monetary(
        string="Réel des mois clos", compute="_compute_amounts", currency_field="currency_id"
    )
    amount_forecast = fields.Monetary(
        string="Prévu des mois ouverts", compute="_compute_amounts", currency_field="currency_id"
    )
    amount_total = fields.Monetary(
        string="Total de l'horizon", compute="_compute_amounts", currency_field="currency_id"
    )

    _sql_constraints = [
        (
            "position_uniq_per_forecast",
            "unique(forecast_id, position_id)",
            "Un poste n'a qu'une ligne par passe de prévision.",
        ),
    ]

    @api.constrains("position_id", "forecast_id")
    def _check_company(self):
        for line in self:
            if line.position_id.company_id != line.forecast_id.company_id:
                raise ValidationError(
                    _("Le poste et la prévision doivent être de la même société.")
                )

    @api.depends("period_ids.amount", "period_ids.is_closed")
    def _compute_amounts(self):
        for line in self:
            clos = line.period_ids.filtered("is_closed")
            ouverts = line.period_ids - clos
            line.amount_actual = sum(clos.mapped("amount"))
            line.amount_forecast = sum(ouverts.mapped("amount"))
            line.amount_total = line.amount_actual + line.amount_forecast

    @api.model_create_multi
    def create(self, vals_list):
        """⚠️ La grille se pose DANS `create`, jamais par un `default`."""
        lines = super().create(vals_list)
        for line in lines:
            if not line.period_ids:
                line._generate_periods()
        return lines

    def _generate_periods(self):
        """Un mois par mois de l'horizon.

        ⚠️ L'horizon d'une prévision glissante TRAVERSE les exercices : rien ici
        ne doit supposer douze mois ni un début au 1er janvier.
        """
        from dateutil.relativedelta import relativedelta

        Period = self.env["bf.budget.forecast.period"]
        for line in self:
            line.period_ids.unlink()
            debut, fin = line.forecast_id.date_start, line.forecast_id.date_end
            if not debut or not fin:
                continue
            curseur = debut.replace(day=1)
            vals_list, sequence = [], 0
            while curseur <= fin:
                mois_fin = curseur + relativedelta(months=1, days=-1)
                vals_list.append(
                    {
                        "line_id": line.id,
                        "sequence": sequence,
                        "date_start": max(curseur, debut),
                        "date_end": min(mois_fin, fin),
                        "amount_forecast": 0.0,
                    }
                )
                sequence += 1
                curseur += relativedelta(months=1)
            Period.create(vals_list)

    # ------------------------------------------------------------------
    # L'amorce
    # ------------------------------------------------------------------
    def _seed_open_months(self, only_empty=False):
        """Pré-remplit les mois ouverts, pour que la passe mensuelle soit rapide.

        Point d'extension : le socle amorce avec la **moyenne du réel des mois
        clos**, ce qui est grossier mais honnête et ne suppose rien. Un satellite
        qui connaît un calendrier d'engagements datés le remplace par mieux.

        ⚠️ Une prévision qu'il faut ressaisir à la main de bout en bout tous les
        mois cesse d'être refaite après deux passes. L'amorce n'est pas un
        confort, c'est ce qui décide si le module sert.
        """
        for line in self:
            if line.forecast_id.state != "draft":
                continue
            ouverts = line.period_ids.filtered(lambda p: not p.is_closed)
            if only_empty:
                ouverts = ouverts.filtered(lambda p: not p.amount_forecast)
            if not ouverts:
                continue
            for period in ouverts:
                valeur = line._seed_value_for(period)
                if valeur is not None:
                    period.amount_forecast = valeur
        return True

    def _seed_value_for(self, period):
        """La valeur d'amorce d'un mois ouvert, ou None pour ne rien poser."""
        self.ensure_one()
        clos = self.period_ids.filtered("is_closed")
        if not clos:
            return None
        return sum(clos.mapped("amount")) / len(clos)

    def action_view_periods(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Mois de la ligne"),
            "res_model": "bf.budget.forecast.period",
            "view_mode": "list",
            "domain": [("line_id", "=", self.id)],
        }
