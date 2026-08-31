from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models


def _median(values):
    """Médiane d'une liste de nombres. Liste vide : zéro."""
    ordered = sorted(values)
    n = len(ordered)
    if not n:
        return 0.0
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


class Indicator(models.Model):
    """Un relevé mensuel, par société.

    Un taux de roulement n'est pas un chiffre, c'est un chiffre SUR UNE
    PÉRIODE. Le stocker par mois plutôt que le recalculer à la volée donne
    l'historique sans lequel la tendance ne se voit pas.
    """

    _name = "bf.ex.indicator"
    _description = "Indicateur de rétention"
    _order = "period desc, company_id"
    _rec_name = "period"

    company_id = fields.Many2one(
        "res.company", string="Société", required=True, index=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", readonly=True,
    )
    period = fields.Date(
        string="Mois", required=True, index=True,
        help="Premier jour du mois couvert.",
    )
    headcount_start = fields.Integer(string="Effectif au début")
    headcount_end = fields.Integer(string="Effectif à la fin")
    hires = fields.Integer(string="Arrivées")
    departures = fields.Integer(string="Départs")
    turnover_rate = fields.Float(
        string="Taux de roulement (%)",
        help="Départs du mois sur l'effectif moyen du mois.",
    )
    median_tenure_months = fields.Float(string="Ancienneté médiane (mois)")
    benefit_cost = fields.Monetary(
        string="Coût des avantages", currency_field="currency_id",
        help="Somme des usages confirmés du mois, coût réel.",
    )
    cost_per_employee = fields.Monetary(
        string="Coût par personne", currency_field="currency_id",
    )
    departure_cost_method = fields.Selection(
        [
            ("none", "Non calculé"),
            ("itemized", "Composantes réelles"),
            ("salary_pct", "Pourcentage du salaire annuel"),
        ],
        string="Méthode du coût d'un départ", default="none",
        help="Les composantes réelles et le pourcentage du salaire sont deux "
             "façons d'estimer la MÊME chose. Les additionner compterait deux "
             "fois le même départ.",
    )
    departure_cost = fields.Monetary(
        string="Coût des départs", currency_field="currency_id",
    )

    _sql_constraints = [
        (
            "period_company_uniq",
            "unique(period, company_id)",
            "Un seul relevé par mois et par société.",
        ),
    ]

    # ------------------------------------------------------------------

    @api.model
    def _compute_period(self, company, period):
        """Calculer (ou recalculer) le relevé d'un mois pour une société.

        `period` est ramené au premier du mois. Renvoie l'enregistrement.
        """
        period = period.replace(day=1)
        nxt = period + relativedelta(months=1)
        Employee = self.env["hr.employee"].sudo()

        base = [("company_id", "=", company.id)]
        # Effectif : présent = premier contrat commencé, départ non survenu.
        started = Employee.search(base + [("first_contract_date", "!=", False)])

        def present_on(day):
            return started.filtered(
                lambda e: e.first_contract_date <= day
                and (not e.departure_date or e.departure_date >= day)
            )

        head_start = present_on(period)
        head_end = present_on(nxt - relativedelta(days=1))
        hires = started.filtered(lambda e: period <= e.first_contract_date < nxt)
        departures = Employee.search(base + [
            ("departure_date", ">=", period), ("departure_date", "<", nxt),
        ])

        avg_head = (len(head_start) + len(head_end)) / 2.0
        turnover = 100.0 * len(departures) / avg_head if avg_head else 0.0

        ref = nxt - relativedelta(days=1)
        tenures = [
            (ref.year - e.first_contract_date.year) * 12
            + (ref.month - e.first_contract_date.month)
            for e in head_end
        ]

        usages = self.env["bf.ex.usage"].sudo().search([
            ("company_id", "=", company.id),
            ("state", "=", "confirmed"),
            ("date", ">=", period),
            ("date", "<", nxt),
        ])
        cost = sum(usages.mapped("amount"))

        vals = {
            "company_id": company.id,
            "period": period,
            "headcount_start": len(head_start),
            "headcount_end": len(head_end),
            "hires": len(hires),
            "departures": len(departures),
            "turnover_rate": turnover,
            "median_tenure_months": _median(tenures),
            "benefit_cost": cost,
            "cost_per_employee": cost / len(head_end) if head_end else 0.0,
        }
        record = self.sudo().search([
            ("company_id", "=", company.id), ("period", "=", period),
        ], limit=1)
        if record:
            record.write(vals)
        else:
            record = self.sudo().create(vals)
        return record

    @api.model
    def _cron_build_indicators(self):
        """Relever le mois qui vient de se terminer, pour chaque société."""
        today = fields.Date.context_today(self)
        previous = (today.replace(day=1) - relativedelta(days=1)).replace(day=1)
        for company in self.env["res.company"].sudo().search([]):
            self._compute_period(company, previous)
        return True

    def action_recompute(self):
        for record in self:
            self._compute_period(record.company_id, record.period)
        return True

    @api.model
    def action_build_current_month(self):
        today = fields.Date.context_today(self)
        return self._compute_period(self.env.company, today.replace(day=1))
