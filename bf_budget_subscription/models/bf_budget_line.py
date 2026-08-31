from odoo import _, api, fields, models


class BfBudgetLine(models.Model):
    """Le calendrier des engagements datés entre dans les deux montants qui en dépendent.

    Le socle rendait 0 pour l'engagé au-delà du comptabilisé, et None pour le
    théorique de calendrier. Ce satellite remplit les deux, sans toucher au reste.
    """

    _inherit = "bf.budget.line"

    subscription_ids = fields.Many2many(
        "subscription.subscription",
        string="Abonnements rattachés",
        compute="_compute_subscription_ids",
        help="Les abonnements dont le poste est celui de cette ligne. La liste se "
        "calcule : rattacher un abonnement à un poste suffit, il n'y a rien à "
        "reporter dans les budgets.",
    )
    subscription_count = fields.Integer(compute="_compute_subscription_ids")
    subscription_no_calendar_ids = fields.Many2many(
        "subscription.subscription",
        string="Abonnements sans échéancier",
        compute="_compute_subscription_ids",
        help="Abonnements à la demande rattachés à ce poste : ils dépensent sans "
        "calendrier, donc le théorique de calendrier ne les couvre pas.",
    )
    has_subscription_without_calendar = fields.Boolean(compute="_compute_subscription_ids")

    subscription_due_to_date = fields.Monetary(
        string="Échu à ce jour",
        compute="_compute_subscription_amounts",
        currency_field="currency_id",
    )
    subscription_upcoming = fields.Monetary(
        string="Renouvellements à venir",
        compute="_compute_subscription_amounts",
        currency_field="currency_id",
        help="Ce qui tombera d'ici la fin de l'exercice. C'est connu, daté et "
        "contractuel : c'est déjà engagé.",
    )
    subscription_period_total = fields.Monetary(
        string="Engagements de la période",
        compute="_compute_subscription_amounts",
        currency_field="currency_id",
    )

    @api.depends("position_id", "source", "company_id")
    def _compute_subscription_ids(self):
        Subscription = self.env["subscription.subscription"]
        for line in self:
            if line.source != "accounting" or not line.position_id:
                line.subscription_ids = Subscription
                line.subscription_no_calendar_ids = Subscription
                line.subscription_count = 0
                line.has_subscription_without_calendar = False
                continue
            subscriptions = Subscription.sudo().search(
                [
                    ("budget_position_id", "=", line.position_id.id),
                    ("company_id", "in", (line.company_id.id, False)),
                ]
            )
            line.subscription_ids = subscriptions
            line.subscription_count = len(subscriptions)
            # ⚠️ Seuls les abonnements ENCORE actifs sans échéancier sont un
            # angle mort : un « à la demande » résilié ne coûtera plus rien.
            blind = subscriptions.filtered(
                lambda s: not s.budget_has_calendar and s.state in ("active", "paused")
            )
            line.subscription_no_calendar_ids = blind
            line.has_subscription_without_calendar = bool(blind)

    @api.depends(
        "subscription_ids",
        "budget_id.date_start",
        "budget_id.date_end",
        "currency_id",
    )
    def _compute_subscription_amounts(self):
        today = fields.Date.context_today(self)
        for line in self:
            due = upcoming = total = 0.0
            budget = line.budget_id
            if line.subscription_ids and budget.date_start and budget.date_end:
                company = budget.company_id
                currency = line.currency_id
                for sub in line.subscription_ids:
                    if today >= budget.date_start:
                        due += sub._budget_amount_between(
                            budget.date_start, min(today, budget.date_end), currency, company
                        )
                    if today < budget.date_end:
                        upcoming += sub._budget_amount_between(
                            max(today, budget.date_start) , budget.date_end, currency, company
                        )
                    total += sub._budget_amount_between(
                        budget.date_start, budget.date_end, currency, company
                    )
                # Une échéance tombant exactement aujourd'hui est comptée dans
                # l'échu ; on la retire du « à venir » pour ne pas la compter deux fois.
                upcoming = max(0.0, total - due)
            line.subscription_due_to_date = due
            line.subscription_upcoming = upcoming
            line.subscription_period_total = total

    def _get_extra_commitments(self):
        """Les renouvellements à venir sont déjà engagés.

        ⚠️ On n'ajoute QUE l'à-venir. L'échu est déjà dans le réalisé dès que la
        facture est comptabilisée ; l'ajouter aussi doublerait la dépense la plus
        prévisible du budget, celle qu'on croit justement la mieux tenue.
        """
        base = super()._get_extra_commitments()
        return base + self.subscription_upcoming

    def _get_calendar_theoretical(self):
        """« Ce qui était dû à ce jour », plutôt qu'une fraction du temps écoulé.

        La part du plan qui n'est adossée à aucun engagement daté reste au
        prorata : un poste où les abonnements ne pèsent que la moitié du budget
        ne doit pas voir l'autre moitié disparaître du théorique.
        """
        self.ensure_one()
        calendared = self.subscription_ids.filtered("budget_has_calendar")
        if not calendared:
            return super()._get_calendar_theoretical()
        today = fields.Date.context_today(self)
        uncovered_plan = max(0.0, self.amount_planned - self.subscription_period_total)
        remainder = 0.0
        if uncovered_plan:
            span = (self.budget_id.date_end - self.budget_id.date_start).days + 1
            elapsed = (min(today, self.budget_id.date_end) - self.budget_id.date_start).days + 1
            elapsed = max(0, min(elapsed, span))
            remainder = uncovered_plan * elapsed / span if span else 0.0
        return self.subscription_due_to_date + remainder

    def action_view_subscriptions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Abonnements du poste"),
            "res_model": "subscription.subscription",
            "view_mode": "list,form",
            "domain": [("id", "in", self.subscription_ids.ids)],
        }
