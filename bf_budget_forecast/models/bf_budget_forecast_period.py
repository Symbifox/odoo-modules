from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BfBudgetForecastPeriod(models.Model):
    """Un mois d'une ligne de prévision : la cellule où tout se joue.

    🔴 DEUX MONTANTS QUI NE SE MÉLANGENT PAS.

    `amount_forecast` est **stocké** : c'est ce qu'on a décidé de prévoir, et ça
    doit survivre au passage du temps. Même une fois le mois clos, on ne l'écrase
    pas — c'est lui qui permet de dire « on avait prévu 900, il en est venu
    1 240 ».

    `amount_actual` n'est **jamais** stocké : il se relit dans les livres à
    chaque affichage, comme partout ailleurs dans ce module.
    """

    _name = "bf.budget.forecast.period"
    _description = "Mois d'une ligne de prévision"
    _order = "line_id, sequence, date_start"

    line_id = fields.Many2one(
        "bf.budget.forecast.line", required=True, ondelete="cascade", index=True
    )
    forecast_id = fields.Many2one(related="line_id.forecast_id", store=True, index=True)
    position_id = fields.Many2one(related="line_id.position_id", store=True)
    company_id = fields.Many2one(related="line_id.company_id", store=True)
    currency_id = fields.Many2one(related="line_id.currency_id")

    sequence = fields.Integer(default=0)
    date_start = fields.Date(required=True)
    date_end = fields.Date(required=True)
    name = fields.Char(compute="_compute_name", store=True)

    amount_forecast = fields.Monetary(
        string="Prévu", currency_field="currency_id",
        help="Ce qu'on a décidé de prévoir pour ce mois. Conservé même après la "
        "clôture du mois : c'est la trace de ce qu'on croyait.",
    )
    amount_actual = fields.Monetary(
        string="Réel", compute="_compute_amount_actual", currency_field="currency_id"
    )
    is_closed = fields.Boolean(
        string="Mois clos", compute="_compute_is_closed", store=True,
        help="Un mois qui finit à la date d'arrêt du réel ou avant.",
    )
    amount = fields.Monetary(
        string="Retenu", compute="_compute_amount", currency_field="currency_id",
        help="Le réel si le mois est clos, la prévision sinon.",
    )
    variance = fields.Monetary(
        string="Écart à la prévision", compute="_compute_amount",
        currency_field="currency_id",
        help="Sur un mois clos : le réel moins ce qu'on avait prévu.",
    )

    @api.depends("date_start")
    def _compute_name(self):
        for period in self:
            period.name = period.date_start.strftime("%Y-%m") if period.date_start else ""

    @api.depends("date_end", "forecast_id.actuals_through")
    def _compute_is_closed(self):
        for period in self:
            arret = period.forecast_id.actuals_through
            period.is_closed = bool(arret and period.date_end <= arret)

    @api.depends("date_start", "date_end", "position_id.account_ids", "company_id")
    def _compute_amount_actual(self):
        """Le réel du mois, relu dans la comptabilité. Jamais stocké."""
        for period in self:
            comptes = period.position_id.account_ids
            if not comptes or not period.date_start:
                period.amount_actual = 0.0
                continue
            groupes = period.env["account.move.line"].sudo()._read_group(
                [
                    ("parent_state", "=", "posted"),
                    ("account_id", "in", comptes.ids),
                    ("date", ">=", period.date_start),
                    ("date", "<=", period.date_end),
                    ("company_id", "=", period.company_id.id),
                ],
                aggregates=["balance:sum"],
            )
            period.amount_actual = (groupes[0][0] if groupes else 0.0) or 0.0

    @api.depends("is_closed", "amount_actual", "amount_forecast")
    def _compute_amount(self):
        for period in self:
            period.amount = period.amount_actual if period.is_closed else period.amount_forecast
            period.variance = (
                period.amount_actual - period.amount_forecast if period.is_closed else 0.0
            )

    def write(self, vals):
        """🔴 Un mois clos ne se re-prévoit pas, et une passe publiée ne bouge plus.

        Les deux gardes disent la même chose autrement : une prévision passée est
        une trace, pas un brouillon. La corriger après coup effacerait la seule
        chose que la prévision glissante apporte.
        """
        if "amount_forecast" in vals:
            fige = self.filtered(lambda p: p.forecast_id.state != "draft")
            if fige:
                raise UserError(
                    _("« %(name)s » appartient à une passe publiée : ses chiffres "
                      "ne se retouchent plus.", name=fige[0].forecast_id.display_name)
                )
            # ⚠️ COPIER N'EST PAS PRÉVOIR. Le report d'une passe à la suivante
            # recopie la prévision HISTORIQUE dans des mois qui sont désormais
            # clos : c'est la mémoire du millésime, et sans elle la comparaison
            # « on avait prévu 900, il en est venu 1 240 » disparaît dès la
            # deuxième passe. Le drapeau n'est posé que par `_carry_line`.
            if not self.env.context.get("bf_budget_forecast_carry"):
                clos = self.filtered("is_closed")
                if clos:
                    raise UserError(
                        _("Le mois %(mois)s est clos : son réel est connu, le prévoir "
                          "n'a plus de sens.", mois=clos[0].name)
                    )
        return super().write(vals)
