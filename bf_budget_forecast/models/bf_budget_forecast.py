from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# Les champs qu'un millésime publié ne laisse plus toucher. Un millésime est la
# trace de ce qu'on croyait à une date : le retoucher efface la seule chose
# qu'une prévision glissante a d'utile.
FROZEN_FIELDS = {"date_start", "date_end", "actuals_through", "line_ids", "company_id"}


class BfBudgetForecast(models.Model):
    """Une passe de prévision, sur un horizon qui traverse les exercices.

    🔴 CE QUI SE STOCKE ET CE QUI NE SE STOCKE PAS.

    Le socle budgétaire ne stocke rien du réel : il le recalcule. Une prévision
    obéit à la même règle vue de l'autre côté — elle stocke la **décision**
    (ce qu'on prévoit) et continue de **calculer** le fait (ce qui est arrivé).

    Sans ce stockage, la question centrale d'une prévision glissante n'aurait pas
    de réponse : « qu'est-ce qu'on croyait en mars pour le mois de juin ? » ne se
    reconstitue à partir de rien.
    """

    _name = "bf.budget.forecast"
    _description = "Prévision glissante"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_start desc, vintage desc, id desc"

    name = fields.Char(required=True, tracking=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company,
        tracking=True, index=True,
    )
    currency_id = fields.Many2one(related="company_id.currency_id", string="Devise")

    date_start = fields.Date(string="Début de l'horizon", required=True, tracking=True)
    date_end = fields.Date(string="Fin de l'horizon", required=True, tracking=True)
    actuals_through = fields.Date(
        string="Réel arrêté au",
        required=True,
        tracking=True,
        help="Les mois qui finissent à cette date ou avant sont du réel, relu "
        "dans la comptabilité. Les suivants sont de la prévision.",
    )

    state = fields.Selection(
        [
            ("draft", "Brouillon"),
            ("published", "Publiée"),
            ("superseded", "Remplacée"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    vintage = fields.Integer(string="Millésime", default=1, readonly=True)
    previous_id = fields.Many2one(
        "bf.budget.forecast", string="Passe précédente", readonly=True, ondelete="set null"
    )
    next_ids = fields.One2many("bf.budget.forecast", "previous_id", string="Passes suivantes")

    line_ids = fields.One2many(
        "bf.budget.forecast.line", "forecast_id", string="Lignes", copy=True
    )
    line_count = fields.Integer(compute="_compute_totals")

    amount_actual = fields.Monetary(
        string="Réel à ce jour", compute="_compute_totals", currency_field="currency_id"
    )
    amount_forecast = fields.Monetary(
        string="Prévu sur les mois ouverts", compute="_compute_totals", currency_field="currency_id"
    )
    amount_total = fields.Monetary(
        string="Total de l'horizon", compute="_compute_totals", currency_field="currency_id"
    )
    month_count = fields.Integer(compute="_compute_totals")
    closed_month_count = fields.Integer(compute="_compute_totals")

    # ------------------------------------------------------------------
    @api.depends(
        "line_ids.amount_actual", "line_ids.amount_forecast", "line_ids.amount_total",
        "line_ids.period_ids.is_closed",
    )
    def _compute_totals(self):
        for forecast in self:
            lines = forecast.line_ids
            forecast.line_count = len(lines)
            forecast.amount_actual = sum(lines.mapped("amount_actual"))
            forecast.amount_forecast = sum(lines.mapped("amount_forecast"))
            forecast.amount_total = sum(lines.mapped("amount_total"))
            periods = lines[:1].period_ids
            forecast.month_count = len(periods)
            forecast.closed_month_count = len(periods.filtered("is_closed"))

    @api.constrains("date_start", "date_end", "actuals_through")
    def _check_dates(self):
        for forecast in self:
            if forecast.date_end < forecast.date_start:
                raise ValidationError(
                    _("La fin de l'horizon ne peut pas précéder son début.")
                )
            if forecast.actuals_through > fields.Date.context_today(forecast):
                raise ValidationError(
                    _(
                        "Le réel ne peut pas être arrêté dans le futur : "
                        "la comptabilité ne l'a pas encore vu."
                    )
                )
            if forecast.actuals_through >= forecast.date_end:
                raise ValidationError(
                    _(
                        "Si le réel est arrêté à la fin de l'horizon, il ne reste "
                        "aucun mois à prévoir : ce n'est plus une prévision."
                    )
                )

    # ------------------------------------------------------------------
    def write(self, vals):
        touched = FROZEN_FIELDS & set(vals)
        if touched and "state" not in vals:
            frozen = self.filtered(lambda f: f.state in ("published", "superseded"))
            if frozen:
                raise UserError(
                    _(
                        "« %(name)s » est publiée : ses chiffres sont la trace de ce "
                        "qu'on croyait à ce moment-là. Roulez une nouvelle passe "
                        "plutôt que de retoucher celle-ci.",
                        name=frozen[0].display_name,
                    )
                )
        return super().write(vals)

    def unlink(self):
        published = self.filtered(lambda f: f.state != "draft")
        if published:
            raise UserError(
                _("Une passe publiée ne se supprime pas : c'est une trace datée.")
            )
        return super().unlink()

    # ------------------------------------------------------------------
    def action_publish(self):
        for forecast in self:
            if forecast.state != "draft":
                raise UserError(_("Seule une passe au brouillon se publie."))
            if not forecast.line_ids:
                raise UserError(_("Une passe sans ligne ne prévoit rien."))
            forecast.state = "published"
            forecast.message_post(
                body=_("Passe %(n)s publiée, réel arrêté au %(date)s.",
                       n=forecast.vintage, date=forecast.actuals_through)
            )
        return True

    def action_reset_draft(self):
        for forecast in self:
            if forecast.state != "published":
                raise UserError(_("Seule une passe publiée revient au brouillon."))
            if forecast.next_ids:
                raise UserError(
                    _("Une passe déjà roulée ne se rouvre pas : c'est la suivante qui vit.")
                )
            forecast.state = "draft"
        return True

    def action_roll_forward(self):
        """La passe du mois suivant : l'horizon avance, le réel gagne un mois.

        ⚠️ C'est LE geste du module. S'il n'est pas quasi instantané, la
        prévision cesse d'être refaite au bout de deux mois et se met à mentir
        avec l'assurance d'un chiffre officiel.
        """
        self.ensure_one()
        if self.state == "superseded":
            raise UserError(_("Cette passe a déjà été roulée."))
        if self.state == "draft":
            self.action_publish()

        new_start = self.date_start + relativedelta(months=1)
        new_end = self.date_end + relativedelta(months=1)
        new_through = self._month_end(self.actuals_through + relativedelta(months=1))
        today = fields.Date.context_today(self)
        if new_through > today:
            new_through = self._month_end(today + relativedelta(months=-1))

        suivante = self.create(
            {
                "name": _("%(name)s — %(date)s", name=self._base_name(), date=new_through),
                "company_id": self.company_id.id,
                "date_start": new_start,
                "date_end": new_end,
                "actuals_through": new_through,
                "vintage": self.vintage + 1,
                "previous_id": self.id,
            }
        )
        for line in self.line_ids:
            suivante._carry_line(line)
        self.state = "superseded"
        self.message_post(
            body=_("Roulée vers la passe %(n)s.", n=suivante.vintage)
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "bf.budget.forecast",
            "res_id": suivante.id,
            "view_mode": "form",
        }

    def _base_name(self):
        self.ensure_one()
        return (self.name or "").split(" — ")[0]

    @api.model
    def _month_end(self, day):
        return day + relativedelta(day=31)

    def _carry_line(self, source_line):
        """Reporte une ligne d'une passe à la suivante.

        ⚠️ Les mois qui existaient déjà gardent EXACTEMENT leur chiffre, y
        compris ceux qui viennent de se clore : c'est cette continuité qui rend
        deux passes comparables. Seul le mois neuf en queue d'horizon est amorcé.
        """
        self.ensure_one()
        line = self.env["bf.budget.forecast.line"].create(
            {"forecast_id": self.id, "position_id": source_line.position_id.id}
        )
        anciens = {p.date_start: p.amount_forecast for p in source_line.period_ids}
        # Le drapeau autorise la RECOPIE d'une prévision passée dans un mois clos.
        # Il n'autorise pas à en formuler une nouvelle : voir la garde de
        # `bf.budget.forecast.period.write`.
        a_reporter = line.period_ids.with_context(bf_budget_forecast_carry=True)
        for period in a_reporter:
            if period.date_start in anciens:
                period.amount_forecast = anciens[period.date_start]
        line._seed_open_months(only_empty=True)
        return line

    def action_seed(self):
        for forecast in self:
            if forecast.state != "draft":
                raise UserError(_("On n'amorce que le brouillon."))
            forecast.line_ids._seed_open_months()
        return True

    def action_generate_lines(self):
        """Une ligne par poste de charges de la société."""
        self.ensure_one()
        if self.state != "draft":
            raise UserError(_("Les lignes ne se génèrent que sur un brouillon."))
        positions = self.env["bf.budget.position"].search(
            [("budget_type", "=", "expense"), ("company_id", "=", self.company_id.id)]
        )
        existing = self.line_ids.mapped("position_id")
        created = self.env["bf.budget.forecast.line"]
        for position in positions - existing:
            created |= self.env["bf.budget.forecast.line"].create(
                {"forecast_id": self.id, "position_id": position.id}
            )
        if not created:
            raise UserError(_("Chaque poste a déjà sa ligne."))
        created._seed_open_months()
        return True

    # ------------------------------------------------------------------
    def compare_to(self, other):
        """Ce qu'on croyait alors, contre ce qu'on croit maintenant.

        Aide PURE : rend des dictionnaires, ne rend rien à l'écran. C'est la
        seule chose qu'une prévision glissante apporte vraiment, et elle ne
        serait pas calculable si les passes n'étaient pas stockées.
        """
        self.ensure_one()
        rows = []
        autres = {line.position_id.id: line for line in other.line_ids}
        for line in self.line_ids:
            ancienne = autres.get(line.position_id.id)
            rows.append(
                {
                    "position": line.position_id,
                    "label": line.position_id.display_name,
                    "now": line.amount_total,
                    "before": ancienne.amount_total if ancienne else 0.0,
                    "delta": line.amount_total - (ancienne.amount_total if ancienne else 0.0),
                    "actual_now": line.amount_actual,
                    "actual_before": ancienne.amount_actual if ancienne else 0.0,
                }
            )
        return rows

    def action_open_comparison(self):
        self.ensure_one()
        if not self.previous_id:
            raise UserError(_("Cette passe n'a pas de précédente à comparer."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Comparaison des passes"),
            "res_model": "bf.budget.forecast.line",
            "view_mode": "list",
            "domain": [("forecast_id", "in", (self.id, self.previous_id.id))],
            "context": {"search_default_group_forecast": 1},
        }
