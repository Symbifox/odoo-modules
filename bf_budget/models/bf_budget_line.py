from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class BfBudgetLine(models.Model):
    """Une ligne de budget : un poste, un montant prévu, et quatre montants calculés.

    🔴 LA RÈGLE QUI PROTÈGE LE CHIFFRE : une ligne lit UNE SEULE source.

    Une facture comptabilisée qui porte une distribution analytique produit une
    écriture comptable ET une ligne analytique. Additionner les deux compterait
    chaque dollar deux fois, en silence, sans que rien ne paraisse anormal à
    l'écran. Le champ `source` tranche donc explicitement, et les deux sources
    sont disjointes par construction :

    * `accounting` lit les écritures comptabilisées ;
    * `internal_cost` ne lit QUE les lignes analytiques **sans pièce comptable**
      (`move_line_id = False`), c'est-à-dire les feuilles de temps et les
      écritures analytiques saisies à la main.

    Aucune ligne analytique adossée à une écriture n'entre donc jamais deux fois.
    """

    _name = "bf.budget.line"
    _description = "Budget line"
    _order = "budget_id, sequence, id"

    budget_id = fields.Many2one(
        "bf.budget", required=True, ondelete="cascade", index=True
    )
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(related="budget_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="budget_id.currency_id")
    date_start = fields.Date(related="budget_id.date_start", store=True)
    date_end = fields.Date(related="budget_id.date_end", store=True)
    budget_type = fields.Selection(related="budget_id.budget_type", store=True)
    state = fields.Selection(related="budget_id.state", store=True)

    source = fields.Selection(
        [
            ("accounting", "Accounting"),
            ("internal_cost", "Internal cost (outside accounting)"),
        ],
        required=True,
        default="accounting",
        help="Accounting: entries posted on the budget item's "
             "accounts.\nInternal cost: analytic lines without a journal "
             "entry, that is timesheets and manual analytic entries. The "
             "two sources are separate: no amount can be counted twice.",
    )
    position_id = fields.Many2one(
        "bf.budget.position",
        string="Budget item",
        ondelete="restrict",
        index=True,
        domain="[('budget_type', '=', budget_type), ('company_id', '=', company_id)]",
    )
    analytic_account_ids = fields.Many2many(
        "account.analytic.account",
        string="Analytic accounts",
        help="Optional for accounting: restricts reading to entries whose "
             "analytic distribution names one of these accounts. Required "
             "for an internal cost line, where it is the only available "
             "axis.",
    )
    name = fields.Char(string="Budget item", compute="_compute_name", store=True)

    amount_planned = fields.Monetary(
        string="Planned",
        compute="_compute_amount_planned",
        inverse="_inverse_amount_planned",
        store=True,
        readonly=False,
        currency_field="currency_id",
    )
    period_ids = fields.One2many(
        "bf.budget.line.period", "line_id", string="Breakdown", copy=True
    )

    amount_actual = fields.Monetary(
        string="Actual", compute="_compute_amounts", currency_field="currency_id"
    )
    amount_committed = fields.Monetary(
        string="Committed", compute="_compute_amounts", currency_field="currency_id"
    )
    amount_theoretical = fields.Monetary(
        string="Theoretical", compute="_compute_amounts", currency_field="currency_id"
    )
    theoretical_basis = fields.Selection(
        [("calendar", "Commitment calendar"), ("prorata", "Prorated "
                                                                 "from "
                                                                 "the "
                                                                 "breakdown")],
        string="Theoretical basis",
        compute="_compute_amounts",
        help="The module states what it relies on. A pro rata on an "
             "annual expense would cry overrun every renewal month.",
    )
    amount_variance = fields.Monetary(
        string="Variance from plan",
        compute="_compute_amounts",
        currency_field="currency_id",
        help="Planned minus committed. Positive: budget remains.",
    )
    amount_drift = fields.Monetary(
        string="Variance from theoretical",
        compute="_compute_amounts",
        currency_field="currency_id",
        help="Committed minus theoretical. Positive on an expense: "
             "spending is running faster than planned at this point of "
             "the fiscal year.",
    )
    drift_pct = fields.Float(string="Variance from theoretical (%)", compute="_compute_amounts")
    is_alert = fields.Boolean(string="Flagged", compute="_compute_amounts", search="_search_is_alert")

    unvalued_hours = fields.Float(
        string="Uncosted hours",
        compute="_compute_unvalued_hours",
        help="Logged hours whose cost is zero. Odoo costs time from the "
             "employee's hourly cost: when that rate is missing, the line "
             "reads zero and looks perfectly normal.",
    )
    has_unvalued_time = fields.Boolean(compute="_compute_unvalued_hours")

    overrun_accepted = fields.Boolean(
        string="Accepted overrun",
        help="The overrun is known and accepted: the line is no longer "
             "flagged.",
    )
    overrun_reason = fields.Text(string="Overrun reason")

    _sql_constraints = [
        (
            "planned_positive",
            "CHECK(amount_planned >= 0)",
            "A planned amount cannot be negative: the budget item's type "
            "(expenses or revenue) carries the direction, not the sign.",
        ),
    ]

    # ------------------------------------------------------------------
    # Cohérence
    # ------------------------------------------------------------------
    @api.constrains("source", "position_id", "analytic_account_ids")
    def _check_source_requirements(self):
        for line in self:
            if line.source == "accounting" and not line.position_id:
                raise ValidationError(
                    _("A line read from accounting needs a budget item.")
                )
            if line.source == "internal_cost" and not line.analytic_account_ids:
                raise ValidationError(
                    _(
                        "An internal cost line needs at least one "
                        "analytic account: it is its only axis."
                    )
                )

    @api.constrains("position_id", "budget_id")
    def _check_position_direction(self):
        for line in self:
            position = line.position_id
            if position and position.budget_type != line.budget_id.budget_type:
                raise ValidationError(
                    _(
                        "The budget item \"%(position)s\" does not fit a "
                        "budget of type %(kind)s.",
                        position=position.display_name,
                        kind=dict(line.budget_id._fields["budget_type"]._description_selection(
                            line.env)).get(line.budget_id.budget_type),
                    )
                )

    @api.depends("position_id", "analytic_account_ids", "source")
    def _compute_name(self):
        for line in self:
            if line.position_id:
                label = line.position_id.display_name
            else:
                label = _("Internal cost")
            if line.analytic_account_ids:
                label = "%s — %s" % (
                    label,
                    ", ".join(line.analytic_account_ids.mapped("display_name")[:3]),
                )
            line.name = label

    # ------------------------------------------------------------------
    # Prévu et répartition
    # ------------------------------------------------------------------
    @api.depends("period_ids.amount_planned")
    def _compute_amount_planned(self):
        for line in self:
            if line.period_ids:
                line.amount_planned = sum(line.period_ids.mapped("amount_planned"))
            else:
                line.amount_planned = line.amount_planned or 0.0

    def _inverse_amount_planned(self):
        """Saisir un total le répartit également sur les mois de l'exercice."""
        for line in self:
            if not line.period_ids:
                line._generate_periods()
            line._spread_evenly(line.amount_planned)

    def _spread_evenly(self, total):
        """Répartit un total sur les périodes, le reliquat de cents au dernier mois.

        ⚠️ On ne redistribue pas le reliquat en douce sur chaque mois : un total qui
        ne se divise pas par douze laisse des cents derrière lui, et c'est le
        dernier mois qui les reprend, visiblement.
        """
        self.ensure_one()
        periods = self.period_ids.sorted("date_start")
        if not periods:
            return
        currency = self.currency_id or self.env.company.currency_id
        share = currency.round(total / len(periods))
        running = 0.0
        for period in periods[:-1]:
            period.amount_planned = share
            running += share
        periods[-1].amount_planned = currency.round(total - running)

    def _generate_periods(self):
        """Une période par mois de l'exercice, à zéro."""
        from dateutil.relativedelta import relativedelta

        Period = self.env["bf.budget.line.period"]
        for line in self:
            line.period_ids.unlink()
            start = line.budget_id.date_start
            end = line.budget_id.date_end
            if not start or not end:
                continue
            cursor = start.replace(day=1)
            vals_list = []
            sequence = 0
            while cursor <= end:
                period_end = cursor + relativedelta(months=1, days=-1)
                vals_list.append(
                    {
                        "line_id": line.id,
                        "sequence": sequence,
                        "date_start": max(cursor, start),
                        "date_end": min(period_end, end),
                        "amount_planned": 0.0,
                    }
                )
                sequence += 1
                cursor += relativedelta(months=1)
            Period.create(vals_list)

    @api.model_create_multi
    def create(self, vals_list):
        """⚠️ Les périodes se posent DANS `create`, jamais par un `default`.

        Un champ calculé stocké doublé d'un `default` ne joue jamais son calcul à
        la création : le défaut gagne, en silence.
        """
        lines = super().create(vals_list)
        for line, vals in zip(lines, vals_list):
            if not line.period_ids:
                line._generate_periods()
                if vals.get("amount_planned"):
                    line._spread_evenly(vals["amount_planned"])
        return lines

    def action_spread_evenly(self):
        for line in self:
            line._spread_evenly(line.amount_planned)
        return True

    def action_regenerate_periods(self):
        for line in self:
            if line.state != "draft":
                raise UserError(
                    _("The breakdown can only be redone on a draft budget.")
                )
            total = line.amount_planned
            line._generate_periods()
            line._spread_evenly(total)
        return True

    # ------------------------------------------------------------------
    # Les quatre montants
    # ------------------------------------------------------------------
    def _sign(self):
        """Le sens qui rend un montant lisible.

        Une charge est un débit : son solde comptable est positif, et son montant
        analytique est négatif. Un produit fait l'inverse. On ramène tout à
        « dépensé » ou « encaissé », positif.
        """
        self.ensure_one()
        if self.source == "internal_cost":
            return -1.0 if self.budget_type == "expense" else 1.0
        return 1.0 if self.budget_type == "expense" else -1.0

    def _actual_domain(self):
        """Le domaine de lecture du réel, sur la seule source de cette ligne."""
        self.ensure_one()
        budget = self.budget_id
        if self.source == "internal_cost":
            column = self._analytic_column_domain()
            return [
                ("move_line_id", "=", False),
                ("date", ">=", budget.date_start),
                ("date", "<=", budget.date_end),
                ("company_id", "=", budget.company_id.id),
            ] + column
        domain = [
            ("parent_state", "=", "posted"),
            ("account_id", "in", self.position_id.account_ids.ids),
            ("date", ">=", budget.date_start),
            ("date", "<=", budget.date_end),
            ("company_id", "=", budget.company_id.id),
        ]
        if self.analytic_account_ids:
            # ⚠️ `analytic_distribution` est un champ JSON : on ne le filtre pas
            # en domaine. On passe par les lignes analytiques, qui portent le lien
            # `move_line_id` vers l'écriture.
            analytic_lines = self.env["account.analytic.line"].search(
                [
                    ("move_line_id", "!=", False),
                    ("date", ">=", budget.date_start),
                    ("date", "<=", budget.date_end),
                ]
                + self._analytic_column_domain()
            )
            domain.append(("id", "in", analytic_lines.mapped("move_line_id").ids))
        return domain

    def _analytic_column_domain(self):
        """Le domaine sur la bonne colonne de plan analytique.

        ⚠️ `account.analytic.line` n'a pas un champ « compte analytique » unique :
        elle porte une colonne par plan racine, dont le nom se demande au plan.
        Écrire `account_id` en dur marche tant qu'il n'existe qu'un plan, et casse
        silencieusement au deuxième.
        """
        self.ensure_one()
        by_column = {}
        for account in self.analytic_account_ids:
            column = account.root_plan_id._column_name()
            by_column.setdefault(column, []).append(account.id)
        if not by_column:
            return [("id", "=", False)]
        domain = []
        for index, (column, ids) in enumerate(by_column.items()):
            if index:
                domain.insert(0, "|")
            domain.append((column, "in", ids))
        return domain

    @api.depends("source", "analytic_account_ids", "budget_id.date_start", "budget_id.date_end")
    def _compute_unvalued_hours(self):
        """🔴 Un coût interne à zéro n'est pas forcément un coût interne absent.

        Odoo valorise une feuille de temps dans `amount` à partir du coût horaire
        de l'employé. Quand ce taux n'est pas renseigné — c'est le cas chez la
        plupart des organisations qui viennent d'installer la paie — le montant
        vaut 0,00 $ alors que les heures, elles, sont bien là. La ligne afficherait
        « rien dépensé » et personne ne saurait que c'est un réglage qui manque.
        """
        for line in self:
            line.unvalued_hours = 0.0
            line.has_unvalued_time = False
            if line.source != "internal_cost" or not line.analytic_account_ids:
                continue
            unvalued = line.env["account.analytic.line"].sudo().search(
                line._actual_domain() + [("amount", "=", 0.0), ("unit_amount", ">", 0.0)]
            )
            line.unvalued_hours = sum(unvalued.mapped("unit_amount"))
            line.has_unvalued_time = bool(line.unvalued_hours)

    def _get_extra_commitments(self):
        """Les engagements connus mais pas encore comptabilisés.

        Point d'extension du socle : il rend zéro. Les satellites l'enrichissent
        (bons de commande confirmés, notes de frais approuvées, renouvellements
        d'abonnement à venir). Le socle ne prétend pas les connaître.
        """
        self.ensure_one()
        return 0.0

    def _get_calendar_theoretical(self):
        """Le théorique tiré d'un calendrier d'engagements datés, ou None.

        Le socle n'en connaît aucun et rend None : le théorique retombe alors sur
        la répartition mensuelle saisie, ce qui est déjà bien plus fidèle qu'un
        prorata du temps écoulé sur l'exercice entier.
        """
        self.ensure_one()
        return None

    def _prorata_theoretical(self, today):
        """Le théorique tiré de la répartition mensuelle."""
        self.ensure_one()
        budget = self.budget_id
        if not budget.date_start or today < budget.date_start:
            return 0.0
        if today >= budget.date_end:
            return self.amount_planned
        total = 0.0
        for period in self.period_ids:
            if period.date_end < today:
                total += period.amount_planned
            elif period.date_start <= today <= period.date_end:
                span = (period.date_end - period.date_start).days + 1
                elapsed = (today - period.date_start).days + 1
                total += period.amount_planned * elapsed / span
        return total

    @api.depends(
        "amount_planned",
        "period_ids.amount_planned",
        "position_id.account_ids",
        "analytic_account_ids",
        "source",
        "budget_id.date_start",
        "budget_id.date_end",
        "budget_id.company_id",
        "overrun_accepted",
    )
    def _compute_amounts(self):
        today = fields.Date.context_today(self)
        for line in self:
            line.amount_actual = line._read_actual()
            line.amount_committed = line.amount_actual + line._get_extra_commitments()
            calendar = line._get_calendar_theoretical()
            if calendar is None:
                line.amount_theoretical = line._prorata_theoretical(today)
                line.theoretical_basis = "prorata"
            else:
                line.amount_theoretical = calendar
                line.theoretical_basis = "calendar"
            line.amount_variance = line.amount_planned - line.amount_committed
            line.amount_drift = line.amount_committed - line.amount_theoretical
            base = line.amount_theoretical or line.amount_planned
            line.drift_pct = (line.amount_drift / base * 100.0) if base else 0.0
            line.is_alert = line._evaluate_alert()

    def _read_actual(self):
        self.ensure_one()
        if self.source == "accounting" and not self.position_id:
            return 0.0
        if self.source == "internal_cost" and not self.analytic_account_ids:
            return 0.0
        model = (
            "account.analytic.line" if self.source == "internal_cost" else "account.move.line"
        )
        field = "amount" if self.source == "internal_cost" else "balance"
        groups = self.env[model].sudo()._read_group(
            self._actual_domain(), aggregates=["%s:sum" % field]
        )
        total = groups[0][0] if groups else 0.0
        return (total or 0.0) * self._sign()

    def _evaluate_alert(self):
        """Signalé quand la dérive dépasse À LA FOIS le pourcentage et le plancher.

        Les deux, jamais l'un seul : un pourcentage seul hurle sur les petits
        postes, un montant seul reste muet sur les gros.
        """
        self.ensure_one()
        if self.overrun_accepted or self.state not in ("open", "closed"):
            return False
        budget = self.budget_id
        drift = self.amount_drift if self.budget_type == "expense" else -self.amount_drift
        if drift <= 0:
            return False
        if drift < budget.alert_threshold_amount:
            return False
        base = self.amount_theoretical or self.amount_planned
        if not base:
            return True
        return (drift / base * 100.0) >= budget.alert_threshold_pct

    def _search_is_alert(self, operator, value):
        """`is_alert` est calculé et non stocké : sans ceci, aucun filtre de vue.

        ⚠️ Un filtre de vue de recherche sur un champ calculé non stocké fait
        ÉCHOUER l'installation du module (« Unsearchable field »), et rien dans le
        code ne le laisse voir à la lecture.
        """
        if operator not in ("=", "!="):
            raise UserError(_("Unsupported filter on \"Flagged\"."))
        wanted = bool(value) if operator == "=" else not bool(value)
        candidates = self.search([("state", "in", ("open", "closed"))])
        matching = candidates.filtered(lambda line: line.is_alert == wanted)
        return [("id", "in", matching.ids)]
