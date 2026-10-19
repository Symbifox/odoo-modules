from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

from .departure import METHODS


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
    departures_voluntary = fields.Integer(string="Départs volontaires")
    departures_involuntary = fields.Integer(string="Départs involontaires")
    departures_regrettable = fields.Integer(string="Départs regrettés")
    departure_cost_method = fields.Selection(
        METHODS, string="Méthode du coût d'un départ", default="none",
        help="Les composantes réelles et le pourcentage du salaire sont deux "
             "façons d'estimer la MÊME chose. Les additionner compterait deux "
             "fois le même départ.",
    )
    departures_12m = fields.Integer(string="Départs sur 12 mois")
    departure_cost_hidden = fields.Boolean(
        string="Trop peu de départs pour publier",
        help="Sous le minimum de la méthode, un montant laisserait deviner un salaire.",
    )
    # 🔴 Réservés aux responsables RH. Le minimum de départs cache un mois isolé,
    # mais pas la DIFFÉRENCE entre deux mois : la somme glissante sur douze mois
    # d'un mois à un seul départ, moins celle du mois d'avant, donne le coût de ce
    # départ, donc le salaire de la personne. Un agent RH n'a pas les salaires.
    departure_cost_low = fields.Monetary(
        string="Coût des départs sur 12 mois (bas)", currency_field="currency_id",
        help="Ce qui se mesure, ou le direct seulement.", groups="hr.group_hr_manager",
    )
    departure_cost = fields.Monetary(
        string="Coût des départs sur 12 mois (haut)", currency_field="currency_id",
        help="Le bas, plus l'indirect estimé.", groups="hr.group_hr_manager",
    )
    departure_cost_avg_low = fields.Monetary(string="Par départ (bas)", currency_field="currency_id",
                                             groups="hr.group_hr_manager")
    departure_cost_avg = fields.Monetary(string="Par départ (haut)", currency_field="currency_id",
                                         groups="hr.group_hr_manager")

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
        # Une personne partie est archivée, mais elle était là en début de mois.
        # Une fiche archivée SANS date de départ (doublon, erreur) reste exclue.
        started = Employee.with_context(active_test=False).search(base + [
            ("first_contract_date", "!=", False),
            "|", ("active", "=", True), ("departure_date", "!=", False),
        ])

        def present_on(day):
            return started.filtered(
                lambda e: e.first_contract_date <= day
                and (not e.departure_date or e.departure_date >= day)
            )

        head_start = present_on(period)
        head_end = present_on(nxt - relativedelta(days=1))
        hires = started.filtered(lambda e: period <= e.first_contract_date < nxt)
        # Le départ archive la fiche (assistant de départ d'Odoo) : sans
        # active_test=False, le roulement réel vaudrait toujours zéro.
        departures = Employee.with_context(active_test=False).search(base + [
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

        vals = dict(self._departure_cost_vals(company, departures, nxt), **{
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
        })
        record = self.sudo().search([
            ("company_id", "=", company.id), ("period", "=", period),
        ], limit=1)
        if record:
            record.write(vals)
        else:
            record = self.sudo().create(vals)
        return record

    @api.model
    def _departure_cost_vals(self, company, month_departures, nxt):
        """Le coût des départs, sur douze mois glissants.

        Douze mois et pas le mois : un mois à un seul départ redonnerait le
        salaire de la personne partie. Sous le minimum de la méthode, rien.
        """
        Departure = self.env["bf.ex.departure"].sudo()
        month = Departure._ensure_for(month_departures)
        window = self.env["hr.employee"].sudo().with_context(active_test=False).search([
            ("company_id", "=", company.id),
            ("departure_date", ">=", nxt - relativedelta(months=12)),
            ("departure_date", "<", nxt),
        ])
        records = Departure._ensure_for(window)
        policy = self.env["bf.ex.departure.policy"]._for_company(company)
        vals = {
            "departures_voluntary": len(month.filtered(lambda d: d.kind == "voluntary")),
            "departures_involuntary": len(month.filtered(lambda d: d.kind == "involuntary")),
            "departures_regrettable": len(month.filtered("regrettable")),
            "departure_cost_method": policy.method,
            "departures_12m": len(records),
            "departure_cost_hidden": False,
            "departure_cost_low": 0.0, "departure_cost": 0.0,
            "departure_cost_avg_low": 0.0, "departure_cost_avg": 0.0,
        }
        if policy.method == "none":
            return vals
        if len(records) < policy.min_departures:
            vals["departure_cost_hidden"] = True
            return vals
        low, high = policy._range(records)
        vals.update({
            "departure_cost_low": low, "departure_cost": high,
            "departure_cost_avg_low": low / len(records), "departure_cost_avg": high / len(records),
        })
        return vals

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
