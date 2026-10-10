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
    _description = "Rolling forecast"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_start desc, vintage desc, id desc"

    name = fields.Char(required=True, tracking=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company,
        tracking=True, index=True,
    )
    currency_id = fields.Many2one(related="company_id.currency_id", string="Currency")

    date_start = fields.Date(string="Horizon start", required=True, tracking=True)
    date_end = fields.Date(string="Horizon end", required=True, tracking=True)
    actuals_through = fields.Date(
        string="Actuals through",
        required=True,
        tracking=True,
        help="Months ending on or before this date are actuals, read from "
             "accounting. The following ones are forecast.",
    )

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("published", "Published"),
            ("superseded", "Superseded"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    vintage = fields.Integer(string="Vintage", default=1, readonly=True)
    previous_id = fields.Many2one(
        "bf.budget.forecast", string="Previous version", readonly=True, ondelete="set null"
    )
    next_ids = fields.One2many("bf.budget.forecast", "previous_id", string="Next "
                                                                           "versions")

    line_ids = fields.One2many(
        "bf.budget.forecast.line", "forecast_id", string="Lines", copy=True
    )
    line_count = fields.Integer(compute="_compute_totals")

    amount_actual = fields.Monetary(
        string="Actual to date", compute="_compute_totals", currency_field="currency_id"
    )
    amount_forecast = fields.Monetary(
        string="Forecast over the open months", compute="_compute_totals", currency_field="currency_id"
    )
    amount_total = fields.Monetary(
        string="Horizon total", compute="_compute_totals", currency_field="currency_id"
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
                    _("The horizon end cannot come before its start.")
                )
            if forecast.actuals_through > fields.Date.context_today(forecast):
                raise ValidationError(
                    _(
                        "Actuals cannot be closed in the future: "
                        "accounting has not seen them yet."
                    )
                )
            if forecast.actuals_through >= forecast.date_end:
                raise ValidationError(
                    _(
                        "If actuals are closed at the horizon end, no "
                        "month is left to forecast: it is no longer a "
                        "forecast."
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
                        "\"%(name)s\" is published: its figures record "
                        "what was believed at that time. Roll a new "
                        "version forward rather than editing this one.",
                        name=frozen[0].display_name,
                    )
                )
        return super().write(vals)

    def unlink(self):
        published = self.filtered(lambda f: f.state != "draft")
        if published:
            raise UserError(
                _("A published version cannot be deleted: it is a dated "
                  "record.")
            )
        return super().unlink()

    # ------------------------------------------------------------------
    def action_publish(self):
        for forecast in self:
            if forecast.state != "draft":
                raise UserError(_("Only a draft version can be published."))
            if not forecast.line_ids:
                raise UserError(_("A version without lines forecasts nothing."))
            forecast.state = "published"
            forecast.message_post(
                body=_("Version %(n)s published, actuals through %(date)s.",
                       n=forecast.vintage, date=forecast.actuals_through)
            )
        return True

    def action_reset_draft(self):
        for forecast in self:
            if forecast.state != "published":
                raise UserError(_("Only a published version can be reset "
                                  "to draft."))
            if forecast.next_ids:
                raise UserError(
                    _("A version already rolled forward cannot be "
                      "reopened: the next one is the live one.")
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
            raise UserError(_("This version has already been rolled forward."))
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
                # Hors catalogue : `_base_name` relit ce nom sur « — ». Traduit, un
                # autre séparateur ferait s'empiler les dates à chaque roulement.
                "name": "%s — %s" % (self._base_name(), new_through),
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
            body=_("Rolled forward to version %(n)s.", n=suivante.vintage)
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
                raise UserError(_("Only a draft can be seeded."))
            forecast.line_ids._seed_open_months()
        return True

    def action_generate_lines(self):
        """Une ligne par poste de charges de la société."""
        self.ensure_one()
        if self.state != "draft":
            raise UserError(_("Lines can only be generated on a draft."))
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
            raise UserError(_("Each budget item already has its forecast line."))
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
            raise UserError(_("This version has no previous one to "
                              "compare with."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Version comparison"),
            "res_model": "bf.budget.forecast.line",
            "view_mode": "list",
            "domain": [("forecast_id", "in", (self.id, self.previous_id.id))],
            "context": {"search_default_group_forecast": 1},
        }
